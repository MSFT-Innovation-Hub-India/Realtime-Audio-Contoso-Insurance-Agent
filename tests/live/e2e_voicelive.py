"""Live end-to-end test of VoiceLiveModelClient against Azure Voice Live and the real tools.

  1. typed question -> claims tool (Azure SQL)
  2. spoken question (synthetic speech) with coughs injected during the answer -> grievance tool (Jira, read-only)
  3. a typed message interrupts an in-flight response

Requires `az login` and .env.  Run:  python tests/live/e2e_voicelive.py
"""

import asyncio
import json
import time

from _helpers import cough, silence, synthesize_speech
from voicelive_client import VoiceLiveModelClient

SYSTEM_PROMPT = "You are a voice assistant for Contoso Insurance. Use the tools. The user name is Srikantan. Keep answers brief."
GRIEVANCE_ID = 10015  # an existing Jira issue id in the grievance project; change for your project
T0 = time.time()


def ts() -> str:
    return f"[{time.time() - T0:5.1f}]"


async def stream(client, pcm: bytes):
    for i in range(0, len(pcm), 4800):  # 100 ms chunks, in real time
        await client.append_input_audio(pcm[i:i + 4800])
        await asyncio.sleep(0.1)


async def wait_for(predicate, timeout=60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


async def main():
    client = VoiceLiveModelClient(system_prompt=SYSTEM_PROMPT)
    stats = {"audio_bytes": 0, "assistant": {}, "user": {}, "tools": [], "turns": [], "errors": []}

    def recorder(name):
        def handler(event):
            if name == "conversation.updated":
                stats["audio_bytes"] += len(event.get("audio", b""))
            elif name == "conversation.text.delta":
                stats["assistant"][event["item_id"]] = stats["assistant"].get(event["item_id"], "") + event["transcript"]
            else:
                if name == "user.turn.started":
                    stats["turns"].append(event["item_id"])
                elif name == "conversation.input.text.done":
                    stats["user"][event["item_id"]] = event["transcript"]
                elif name == "tool.call":
                    stats["tools"].append(event["name"])
                elif name == "error":
                    stats["errors"].append(event["message"])
                print(ts(), "UI<-", name, json.dumps(event, ensure_ascii=False)[:200])
        return handler

    for name in ["conversation.updated", "conversation.interrupted", "user.turn.started", "user.speech.stopped",
                 "conversation.input.text.done", "conversation.text.delta", "tool.call", "error"]:
        client.on(name, recorder(name))

    question = await synthesize_speech(f"Hi, what is the status of my grievance number {GRIEVANCE_ID}?")
    await client.connect()
    await asyncio.sleep(1.5)
    last_answer = lambda: list(stats["assistant"].values())[-1] if stats["assistant"] else ""  # noqa: E731

    print(ts(), "=== TEST 1: typed question -> claims (Azure SQL) ===")
    await client.send_user_message_content([{"type": "input_text", "text": "What is the status of my insurance claims?"}])
    ok1 = await wait_for(lambda: "get_claim_status_summary" in stats["tools"] and client._response_idle.is_set() and len(stats["assistant"]) >= 2)
    await asyncio.sleep(1)
    print(ts(), "test1 ok:", ok1, "|", last_answer()[:250])

    print(ts(), "=== TEST 2: spoken question -> grievance (Jira) + coughs during the answer ===")
    n_answers = len(stats["assistant"])
    done = asyncio.Event()

    async def microphone():
        await stream(client, silence(500) + question + silence(1200))
        for i in range(120):
            if i in (20, 45):
                print(ts(), ">>> injecting cough")
                await stream(client, cough())
            await stream(client, silence(100))
            if done.is_set():
                break

    mic = asyncio.create_task(microphone())
    ok2 = await wait_for(lambda: "get_grievance_status_def" in stats["tools"] and client._response_idle.is_set()
                         and len(stats["assistant"]) >= n_answers + 2)
    await asyncio.sleep(3)
    done.set()
    await mic
    spoken_question = " ".join(stats["user"].values()).lower()
    # coughs must not create extra user turns, and the transcript must be the question that was spoken
    ok2 = ok2 and len(stats["turns"]) == 1 and str(GRIEVANCE_ID) in spoken_question and "status" in spoken_question
    print(ts(), "test2 ok:", ok2, "|", last_answer()[:250])

    print(ts(), "=== TEST 3: typed message interrupts an in-flight response ===")
    await client.send_user_message_content([{"type": "input_text", "text": "Explain in detail all the types of health insurance plans Contoso offers."}])
    await wait_for(lambda: client._active_response_id is not None, 20)
    await asyncio.sleep(1.5)
    await client.send_user_message_content([{"type": "input_text", "text": "Actually, stop. Just say goodbye."}])
    ok3 = await wait_for(lambda: client._response_idle.is_set() and "goodbye" in last_answer().lower())
    print(ts(), "test3 ok:", ok3, "|", last_answer()[:200])

    await client.disconnect()
    print("\n=== SUMMARY ===")
    print("tools:", stats["tools"])
    print("user turns:", len(stats["turns"]), "| transcripts:", stats["user"])
    print("audio bytes:", stats["audio_bytes"], "| errors:", stats["errors"])
    passed = ok1 and ok2 and ok3 and not stats["errors"]
    print("RESULT:", "PASS" if passed else "FAIL")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
