import asyncio
import logging
import re
import traceback
from uuid import uuid4

import chainlit as cl
from chainlit.input_widget import Switch
from chainlit.logger import logger

from azure_auth import warm_up_tokens
from envconfig import DefaultConfig
from ui_config import POWERED_BY, build_config_markdown
from voicelive_client import VoiceLiveModelClient

logging.getLogger("azure").setLevel(logging.WARNING)

CONFIG_PANEL_SETTING = "show_config_panel"

_PERSONA_GRAMMAR = {
    "female": {
        "noun": "a woman",
        "use": "मैं कर सकती हूँ, मैं देख रही हूँ, मैं चेक करती हूँ, मैं बताती हूँ, मैं करूँगी, मैं बता दूँगी, मैं समझ गई",
        "avoid": "सकता, रहा, करता, बताता, करूँगा, दूँगा, गया",
    },
    "male": {
        "noun": "a man",
        "use": "मैं कर सकता हूँ, मैं देख रहा हूँ, मैं चेक करता हूँ, मैं बताता हूँ, मैं करूँगा, मैं बता दूँगा, मैं समझ गया",
        "avoid": "सकती, रही, करती, बताती, करूँगी, दूँगी, गई",
    },
}


def _persona_instructions(gender: str) -> str:
    grammar = _PERSONA_GRAMMAR.get(gender, _PERSONA_GRAMMAR["female"])
    return f"""** Persona and grammatical gender (very important): **
- You speak with a {gender} voice; your persona is {grammar['noun']}.
- When you speak Hindi (or any other language with grammatical gender, such as Marathi, Gujarati, Punjabi or Urdu), ALWAYS use {gender} first-person forms when referring to yourself, e.g. {grammar['use']}. Never use {"masculine" if gender == "female" else "feminine"} forms for yourself ({grammar['avoid']}).
- Keep your own gender even if the user addresses you as "आप ... कर सकते हैं" or with any other form.
- You don't know the user's gender: address the user respectfully as "आप" and use gender-neutral phrasing for them."""

system_prompt = f"""You are a voice AI Assistant representing {DefaultConfig.ai_assistant_organization_name}, tasked with helping users with answers to their queries.
** Some rules you must follow during the conversation: **
- ** You must use the information provided to you in the context of the conversation to respond to the user. DO NOT MAKE STUFF UP. If the context is not sufficient to answer the question, say you don't know**
- ** When you get the user query try to identify the intent of the user and the right tool or function to call that can best answer it.**
- You DO have access to this customer's claims, grievances and Contoso's policy knowledge base through your tools. Never say you don't have access to their account or records; call the right tool instead.
- Before registering a grievance, confirm the category and description with the user.
- Your responses are spoken aloud. Keep them concise and conversational. When a tool returns a table, summarize it instead of reading out the table syntax.
- If the user's input is unclear, noisy or incomplete (for example just a cough or background sound), do not guess; briefly ask them to repeat.
Before responding, review what you are going to say out, and ensure it honours the rules above. If you are unsure, ask the user for clarification.

{_persona_instructions(DefaultConfig.assistant_gender)}

The user name of the person you are interacting with is {DefaultConfig.customer_name}
"""


_ANNOTATION = re.compile(r"[\(\[\*][^\)\]\*]*[\)\]\*]")


def _is_noise_transcript(transcript: str) -> bool:
    """True for empty transcripts or ones made only of non-speech annotations such as '(coughs)' or '[noise]'.
    Annotations around real speech, e.g. '(coughs) What is my claim status?', are not noise."""
    speech = _ANNOTATION.sub(" ", (transcript or "").strip())
    return not re.search(r"\w", speech)


def init_voice_client():
    client = VoiceLiveModelClient(system_prompt=system_prompt)
    cl.user_session.set("track_id", str(uuid4()))
    # server item_id -> chainlit message id / accumulated assistant transcript
    user_messages: dict[str, str] = {}
    assistant_transcripts: dict[str, str] = {}

    async def handle_audio(event):
        """Plays response audio chunks as they are received from the server.
        The browser audio player only exists while the microphone session is active; text-only
        turns outside of it still get the transcript in the chat."""
        audio = event.get("audio")
        if audio and cl.user_session.get("audio_active"):
            await cl.context.emitter.send_audio_chunk(
                cl.OutputAudioChunk(mimeType="pcm16", data=audio, track=cl.user_session.get("track_id"))
            )

    async def handle_interrupt(event):
        """Stops audio playback, when the user barges in or types a new message."""
        cl.user_session.set("track_id", str(uuid4()))
        await cl.context.emitter.send_audio_interrupt()

    async def handle_user_turn_started(event):
        """Creates a user message placeholder as soon as a real user turn is detected, so it is
        always rendered before the assistant's response even though the transcript arrives later."""
        item_id = event["item_id"]
        msg_id = f"user-{item_id}"
        user_messages[item_id] = msg_id
        await cl.Message(content=event["status"], author="user", type="user_message", id=msg_id).send()

    async def handle_user_speech_stopped(event):
        msg_id = user_messages.get(event["item_id"])
        if msg_id:
            await cl.Message(content="🔄 Processing...", author="user", type="user_message", id=msg_id).update()

    async def handle_user_transcript_done(event):
        item_id = event.get("item_id")
        transcript = event.get("transcript") or ""
        msg_id = user_messages.pop(item_id, None)
        if _is_noise_transcript(transcript):
            if msg_id:
                await cl.Message(content="", id=msg_id).remove()
            logger.info(f"Ignoring non-speech input transcript: {transcript!r}")
            return
        message = cl.Message(content=transcript, author="user", type="user_message", id=msg_id or str(uuid4()))
        await (message.update() if msg_id else message.send())

    async def handle_assistant_transcript_delta(event):
        item_id = event["item_id"]
        delta = event.get("transcript")
        if not delta:
            return
        is_new = item_id not in assistant_transcripts
        assistant_transcripts[item_id] = assistant_transcripts.get(item_id, "") + delta
        message = cl.Message(
            content=assistant_transcripts[item_id], author="assistant", type="assistant_message", id=item_id
        )
        await (message.send() if is_new else message.update())

    async def handle_tool_call(event):
        logger.info(f"Calling tool {event['name']} with {event['arguments']}")

    async def handle_error(event):
        await cl.ErrorMessage(content=f"Voice Live error: {event['message']}").send()

    client.on("conversation.updated", handle_audio)
    client.on("conversation.interrupted", handle_interrupt)
    client.on("user.turn.started", handle_user_turn_started)
    client.on("user.speech.stopped", handle_user_speech_stopped)
    client.on("conversation.input.text.done", handle_user_transcript_done)
    client.on("conversation.text.delta", handle_assistant_transcript_delta)
    client.on("tool.call", handle_tool_call)
    client.on("error", handle_error)
    cl.user_session.set("voice_client", client)
    return client


def get_voice_client() -> VoiceLiveModelClient:
    return cl.user_session.get("voice_client") or init_voice_client()


@cl.on_chat_start
async def start():
    init_voice_client()
    asyncio.create_task(asyncio.to_thread(warm_up_tokens))
    # A persistent toggle in the composer's settings (sliders icon), reachable however far the chat scrolls
    await cl.ChatSettings(
        [
            Switch(
                id=CONFIG_PANEL_SETTING,
                label="Show solution configuration panel",
                description="Azure Voice Live model, voice, transcription and the Azure resources used by this agent",
                initial=DefaultConfig.show_config_panel_on_start,
            )
        ]
    ).send()
    if DefaultConfig.show_config_panel_on_start:
        await show_configuration_panel()
    await cl.Message(
        content=f"Hi, Welcome! You are now connected to the Voice AI Assistant representing {DefaultConfig.ai_assistant_organization_name}, "
        f"**{POWERED_BY}** (model `{DefaultConfig.voice_live_model}`).\n\n"
        "Click the microphone icon (or press `P`) to talk. You can also type your question; replies are spoken while the microphone is on.",
        actions=[
            cl.Action(
                name="show_configuration",
                payload={},
                label="Show configuration",
                tooltip="Open the panel with the Azure resources and models used by this agent",
                icon="panel-right-open",
            ),
            cl.Action(
                name="hide_configuration",
                payload={},
                label="Hide configuration",
                tooltip="Collapse the configuration panel",
                icon="panel-right-close",
            ),
        ],
    ).send()


async def show_configuration_panel():
    await cl.ElementSidebar.set_title("Solution configuration")
    await cl.ElementSidebar.set_elements(
        [cl.Text(name="Solution configuration", content=build_config_markdown(), display="side")],
        key="solution-configuration",
    )


async def hide_configuration_panel():
    # an empty element list closes the side panel
    await cl.ElementSidebar.set_elements([])


@cl.action_callback("show_configuration")
async def on_show_configuration(action: cl.Action):
    await show_configuration_panel()


@cl.action_callback("hide_configuration")
async def on_hide_configuration(action: cl.Action):
    await hide_configuration_panel()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    if settings.get(CONFIG_PANEL_SETTING):
        await show_configuration_panel()
    else:
        await hide_configuration_panel()


@cl.on_message
async def on_message(message: cl.Message):
    """Handles text messages typed in the chat window."""
    client = get_voice_client()
    try:
        await client.connect()
        await client.send_user_message_content([{"type": "input_text", "text": message.content}])
    except Exception as e:
        logger.error(traceback.format_exc())
        await cl.ErrorMessage(content=f"Failed to send message to Voice Live API: {e}").send()


@cl.on_audio_start
async def on_audio_start():
    try:
        await get_voice_client().connect()
        cl.user_session.set("audio_active", True)
        return True
    except Exception as e:
        logger.error(traceback.format_exc())
        await cl.ErrorMessage(content=f"Failed to connect to Voice Live API: {e}").send()
        return False


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.InputAudioChunk):
    client = get_voice_client()
    if not cl.user_session.get("audio_active"):
        return
    try:
        if not client.is_connected():
            # the service closed the session (e.g. idle timeout); start a new one
            await client.connect()
        await client.append_input_audio(chunk.data)
    except Exception as e:
        logger.error(traceback.format_exc())
        # stop retrying on every chunk until the user restarts the microphone
        cl.user_session.set("audio_active", False)
        await cl.ErrorMessage(content=f"Failed to send audio to Voice Live API: {e}. Please restart the microphone.").send()


@cl.on_audio_end
async def on_audio_end():
    # The session is kept open so the conversation context is retained for further voice or text turns.
    cl.user_session.set("audio_active", False)
    logger.info("Microphone stopped")


@cl.on_stop
async def on_stop():
    client = cl.user_session.get("voice_client")
    if client and client.is_connected():
        await client.interrupt_response()


@cl.on_chat_end
async def on_chat_end():
    client = cl.user_session.get("voice_client")
    if client:
        await client.disconnect()
