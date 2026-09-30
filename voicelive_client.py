"""
Azure Voice Live API client for a direct GPT-Realtime model (speech-to-speech) session.

- Connects to the Voice Live WebSocket endpoint of a Microsoft Foundry resource using
  Microsoft Entra ID (DefaultAzureCredential -> managed identity in Azure, az login locally).
- Implements client-side function calling against the tools in tools.py.
- Layers several guards so brief noises (coughs, clicks, filler words) don't break a response.
  See README "Handling coughs and brief noises".
"""

import asyncio
import base64
import inspect
import json
import uuid
from collections import defaultdict

import websockets
from chainlit.logger import logger

from azure_auth import VOICE_LIVE_SCOPE, get_access_token
from envconfig import DefaultConfig
from tools import available_functions, tools_list

# Voice Live may emit either the preview or the GA event names depending on api-version.
AUDIO_DELTA_EVENTS = ("response.audio.delta", "response.output_audio.delta")
AUDIO_TRANSCRIPT_DELTA_EVENTS = ("response.audio_transcript.delta", "response.output_audio_transcript.delta")


class VoiceLiveModelClient:
    """Azure Voice Live API client for a GPT-Realtime model with client-side function calling.

    Events dispatched to the UI (processed sequentially, in arrival order):
    - "conversation.updated"      {"audio": bytes}                     response audio chunk to play
    - "conversation.interrupted"  {}                                   stop audio playback
    - "user.turn.started"         {"item_id": str, "status": str}      a real user turn was detected
    - "user.speech.stopped"       {"item_id": str}                     user stopped speaking
    - "conversation.input.text.done" {"item_id": str, "transcript": str}
    - "conversation.text.delta"   {"item_id": str, "transcript": str}  assistant transcript delta
    - "tool.call"                 {"name": str, "arguments": dict}
    - "error"                     {"message": str}
    """

    def __init__(self, system_prompt: str):
        self.ws = None
        self.system_prompt = system_prompt
        self.event_handlers = defaultdict(list)
        self.interrupt_debounce_ms = DefaultConfig.interrupt_debounce_ms

        self._receive_task = None
        self._dispatch_task = None
        self._dispatch_queue: asyncio.Queue = asyncio.Queue()
        self._connect_lock = asyncio.Lock()
        self._closing = False

        self._speech_active = False
        self._current_speech_item_id = None
        self._confirmed_turns = set()
        self._pending_interrupt_task = None

        # Response lifecycle: "requested" covers the gap between asking for a response
        # (client response.create, or server VAD commit) and the server's response.created.
        self._active_response_id = None
        self._response_requested = False
        self._request_seq = 0
        self._response_started = asyncio.Event()
        self._response_idle = asyncio.Event()
        self._response_idle.set()
        self._followup_pending = False
        self._suppressed_response_ids = set()

        self.session_config = {
            "instructions": self.system_prompt,
            "modalities": ["text", "audio"],
            "input_audio_sampling_rate": 24000,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": self._turn_detection_config(),
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "input_audio_transcription": {"model": DefaultConfig.input_transcription_model},
            "voice": {
                "name": DefaultConfig.voice_name,
                "type": "azure-standard",
                "temperature": DefaultConfig.voice_temperature,
            },
            "tools": tools_list,
            "tool_choice": "auto",
        }

    @staticmethod
    def _turn_detection_config() -> dict:
        config = {
            "type": DefaultConfig.vad_type,
            "threshold": DefaultConfig.vad_threshold,
            "prefix_padding_ms": DefaultConfig.vad_prefix_padding_ms,
            "silence_duration_ms": DefaultConfig.vad_silence_duration_ms,
        }
        if DefaultConfig.vad_type.startswith("azure_semantic_vad"):
            config.update(
                {
                    # speech must last this long before it counts as the start of a user turn
                    "speech_duration_ms": DefaultConfig.vad_speech_duration_ms,
                    "remove_filler_words": DefaultConfig.vad_remove_filler_words,
                    "interrupt_response": True,
                    "auto_truncate": True,
                }
            )
        return config

    # ------------------------------------------------------------------ events to the UI

    def on(self, event_name, handler):
        self.event_handlers[event_name].append(handler)

    def dispatch(self, event_name, event):
        """Queues an event for the UI. A single worker drains the queue so UI updates
        (message send/update, audio chunks) are applied strictly in arrival order."""
        self._dispatch_queue.put_nowait((event_name, event))

    async def _dispatch_worker(self):
        while True:
            event_name, event = await self._dispatch_queue.get()
            for handler in self.event_handlers[event_name]:
                try:
                    if inspect.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                except Exception:
                    logger.exception(f"Error in handler for '{event_name}'")

    # ------------------------------------------------------------------ connection

    def is_connected(self) -> bool:
        return self.ws is not None

    def _websocket_url(self) -> str:
        endpoint = DefaultConfig.voice_live_endpoint.rstrip("/")
        endpoint = endpoint.replace("https://", "wss://", 1)
        return (
            f"{endpoint}/voice-live/realtime"
            f"?api-version={DefaultConfig.voice_live_api_version}"
            f"&model={DefaultConfig.voice_live_model}"
        )

    async def connect(self):
        """Opens the Voice Live WebSocket (Entra ID bearer token) and configures the session."""
        async with self._connect_lock:
            if self.is_connected():
                return
            if not DefaultConfig.voice_live_endpoint:
                raise ValueError("AZURE_VOICE_LIVE_ENDPOINT is not configured")

            access_token = await asyncio.to_thread(get_access_token, VOICE_LIVE_SCOPE)
            self.ws = await websockets.connect(
                self._websocket_url(),
                additional_headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-ms-client-request-id": str(uuid.uuid4()),
                },
                max_size=None,
            )
            logger.info(f"Connected to Azure Voice Live API (model={DefaultConfig.voice_live_model})")

            self._closing = False
            if self._dispatch_task is None or self._dispatch_task.done():
                self._dispatch_task = asyncio.create_task(self._dispatch_worker())
            self._receive_task = asyncio.create_task(self.receive())
            await self.send("session.update", {"session": self.session_config})

    async def disconnect(self):
        """Closes the session and stops background tasks. Safe to call when already disconnected."""
        self._closing = True
        ws, self.ws = self.ws, None
        self._cancel_pending_interrupt()
        if ws:
            await ws.close()
            logger.info("Disconnected from the Voice Live API")
        if self._dispatch_task:
            # let queued UI events flush before stopping the worker
            await asyncio.sleep(0)
            self._dispatch_task.cancel()
            self._dispatch_task = None

    async def send(self, event_name, data=None):
        if not self.is_connected():
            raise ConnectionError("Voice Live API is not connected")
        event = {"event_id": f"evt_{uuid.uuid4().hex}", "type": event_name, **(data or {})}
        await self.ws.send(json.dumps(event))

    # ------------------------------------------------------------------ client -> server

    async def append_input_audio(self, array_buffer):
        """Streams microphone audio (pcm16, 24 kHz) to the server input buffer.
        Server VAD decides when a user turn starts/ends and creates the response automatically."""
        if len(array_buffer) > 0:
            await self.send(
                "input_audio_buffer.append",
                {"audio": base64.b64encode(array_buffer).decode("ascii")},
            )

    async def send_user_message_content(self, content):
        """Sends a typed user message and asks the model to respond to it."""
        if not content:
            return
        await self.interrupt_response()
        await self.send(
            "conversation.item.create",
            {"item": {"type": "message", "role": "user", "content": content}},
        )
        await self._request_response()

    async def interrupt_response(self):
        """Stops playback and cancels an in-flight response (e.g. user typed a message or pressed stop)."""
        self.dispatch("conversation.interrupted", {})
        if self._response_requested and not self._active_response_id:
            # a response was asked for but not yet created; wait for it so it can be cancelled
            try:
                await asyncio.wait_for(self._response_started.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass
        if self._active_response_id:
            self._suppressed_response_ids.add(self._active_response_id)
            await self.send("response.cancel")
        try:
            await asyncio.wait_for(self._response_idle.wait(), timeout=3)
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for the active response to finish")

    async def _request_response(self):
        """Sends response.create, or defers it if a response is already active/requested
        (it is then sent when that response is done, so no conversation item is left unanswered)."""
        if self._active_response_id or self._response_requested:
            self._followup_pending = True
            return
        self._followup_pending = False
        self._mark_response_requested()
        await self.send("response.create")

    def _mark_response_requested(self):
        self._response_requested = True
        self._response_started.clear()
        self._response_idle.clear()
        self._request_seq += 1
        asyncio.create_task(self._response_request_watchdog(self._request_seq))

    async def _response_request_watchdog(self, seq: int, timeout_s: float = 8):
        """Don't let a response request that the server never acknowledged block the session."""
        try:
            await asyncio.wait_for(self._response_started.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            if seq == self._request_seq and self._response_requested and not self._active_response_id:
                logger.warning("No response.created received for a response request; resetting state")
                self._response_requested = False
                self._update_response_idle()
                if self._followup_pending and self.is_connected():
                    await self._request_response()

    def _update_response_idle(self):
        if self._active_response_id or self._response_requested:
            self._response_idle.clear()
        else:
            self._response_idle.set()

    # ------------------------------------------------------------------ server -> client

    async def receive(self):
        ws = self.ws
        try:
            async for message in ws:
                await self._handle_server_event(json.loads(message))
        except websockets.ConnectionClosed as e:
            logger.info(f"Voice Live connection closed: {e}")
        except Exception:
            logger.exception("Error in the Voice Live receive loop")
        finally:
            try:
                await ws.close()
            except Exception:
                pass
            if self.ws is ws:
                self.ws = None
            self._active_response_id = None
            self._response_requested = False
            self._response_started.set()
            self._response_idle.set()
            if not self._closing:
                self.dispatch(
                    "error",
                    {"message": "The voice session was closed by the service. It will reconnect on your next input."},
                )

    async def _handle_server_event(self, event: dict):
        event_type = event.get("type")

        if event_type == "error":
            error = event.get("error", {})
            code = error.get("code")
            logger.error(f"Voice Live error: {error}")
            if code == "conversation_already_has_active_response":
                # our response.create lost a race with a server-created response; retry once it's done
                self._response_requested = False
                self._followup_pending = True
                self._update_response_idle()
            elif code != "response_cancel_not_active":
                # cancelling a response that already finished is harmless
                self.dispatch("error", {"message": error.get("message", str(error))})

        elif event_type == "session.updated":
            logger.info("Voice Live session configured")

        elif event_type == "response.created":
            self._active_response_id = event.get("response", {}).get("id")
            self._response_requested = False
            self._response_started.set()
            self._update_response_idle()

        elif event_type in AUDIO_DELTA_EVENTS:
            if event.get("response_id") in self._suppressed_response_ids or not event.get("delta"):
                return  # stale audio from a response the user interrupted
            self.dispatch("conversation.updated", {"audio": base64.b64decode(event["delta"])})

        elif event_type in AUDIO_TRANSCRIPT_DELTA_EVENTS:
            self.dispatch(
                "conversation.text.delta",
                {"item_id": event.get("item_id"), "transcript": event.get("delta", "")},
            )

        elif event_type == "input_audio_buffer.speech_started":
            # Don't stop playback immediately: a cough or click also triggers speech_started.
            # Only treat it as a barge-in if speech persists beyond the debounce window.
            self._speech_active = True
            self._current_speech_item_id = event.get("item_id")
            self._cancel_pending_interrupt()
            self._pending_interrupt_task = asyncio.create_task(
                self._debounced_interrupt(self._current_speech_item_id)
            )

        elif event_type == "input_audio_buffer.speech_stopped":
            self._speech_active = False
            self._cancel_pending_interrupt()
            item_id = event.get("item_id")
            if item_id in self._confirmed_turns:
                self.dispatch("user.speech.stopped", {"item_id": item_id})

        elif event_type == "input_audio_buffer.committed":
            # The server accepted the audio as a user turn and will create a response for it.
            self._mark_response_requested()
            self._confirm_user_turn(event.get("item_id"), status="🔄 Processing...")

        elif event_type == "conversation.item.input_audio_transcription.completed":
            self.dispatch(
                "conversation.input.text.done",
                {"item_id": event.get("item_id"), "transcript": event.get("transcript", "")},
            )

        elif event_type == "conversation.item.input_audio_transcription.failed":
            logger.warning(f"Input audio transcription failed: {event.get('error')}")
            self.dispatch(
                "conversation.input.text.done",
                {"item_id": event.get("item_id"), "transcript": ""},
            )

        elif event_type == "response.done":
            response = event.get("response", {})
            if response.get("id") == self._active_response_id:
                self._active_response_id = None
            self._update_response_idle()
            self._suppressed_response_ids.discard(response.get("id"))

            status = response.get("status")
            if status == "failed":
                logger.error(f"Response failed: {response.get('status_details')}")
            # gpt-realtime models often speak a short preamble first, so function calls can
            # appear at any position in the output (not just output[0]).
            function_calls = [o for o in response.get("output", []) if o.get("type") == "function_call"]
            if status == "completed" and function_calls:
                asyncio.create_task(self._run_function_calls(function_calls))
            elif self._followup_pending and self._response_idle.is_set():
                asyncio.create_task(self._request_response())

    # ------------------------------------------------------------------ interruption handling

    def _confirm_user_turn(self, item_id, status):
        if item_id in self._confirmed_turns:
            return
        self._confirmed_turns.add(item_id)
        if self._active_response_id:
            self._suppressed_response_ids.add(self._active_response_id)
        self.dispatch("conversation.interrupted", {})
        self.dispatch("user.turn.started", {"item_id": item_id, "status": status})

    def _cancel_pending_interrupt(self):
        if self._pending_interrupt_task:
            self._pending_interrupt_task.cancel()
            self._pending_interrupt_task = None

    async def _debounced_interrupt(self, item_id):
        try:
            await asyncio.sleep(self.interrupt_debounce_ms / 1000)
            if self._speech_active and self._current_speech_item_id == item_id:
                self._confirm_user_turn(item_id, status="🎤 Listening...")
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ function calling

    async def _run_function_calls(self, function_calls):
        try:
            for call in function_calls:
                name = call.get("name")
                try:
                    arguments = json.loads(call.get("arguments") or "{}")
                    self.dispatch("tool.call", {"name": name, "arguments": arguments})
                    function_to_call = available_functions[name]
                    # tool functions do blocking I/O (SQL, Search, Jira); keep the event loop free
                    output = await asyncio.to_thread(function_to_call, **arguments)
                except Exception as e:
                    logger.exception(f"Error executing function {name}")
                    output = f"The function {name} failed with error: {e}"
                logger.info(f"Function {name} returned: {str(output)[:500]}")
                await self.send(
                    "conversation.item.create",
                    {
                        "item": {
                            "type": "function_call_output",
                            "call_id": call.get("call_id"),
                            "output": json.dumps(output),
                        }
                    },
                )
            await self._request_response()
        except ConnectionError:
            logger.warning("Connection closed before function call output could be sent")
