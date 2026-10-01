"""Shared helpers for the live tests (require `az login` and a configured .env)."""

import base64
import difflib
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import websockets

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "tests" / "output"
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))

from azure_auth import VOICE_LIVE_SCOPE, get_access_token  # noqa: E402
from envconfig import DefaultConfig  # noqa: E402

SAMPLE_RATE = 24000


def voice_live_url() -> str:
    endpoint = DefaultConfig.voice_live_endpoint.rstrip("/").replace("https://", "wss://", 1)
    return (
        f"{endpoint}/voice-live/realtime?api-version={DefaultConfig.voice_live_api_version}"
        f"&model={DefaultConfig.voice_live_model}"
    )


async def synthesize_speech(text: str, voice: str = "en-US-Andrew:DragonHDLatestNeural", attempts: int = 6) -> bytes:
    """Uses a separate Voice Live session as a TTS engine to produce pcm16 24 kHz 'user' speech.

    A realtime model sometimes *answers* the text instead of reading it, which would feed the app a wrong
    'user' question. The spoken transcript is therefore compared with the requested text and regenerated
    if it doesn't match.
    """
    for attempt in range(1, attempts + 1):
        audio, spoken = await _speak_once(text, voice)
        similarity = difflib.SequenceMatcher(None, _normalize(text), _normalize(spoken)).ratio()
        if similarity >= 0.85:
            return audio
        print(f"[tts] attempt {attempt}: spoken text didn't match (similarity {similarity:.2f}): {spoken[:120]!r}")
    raise RuntimeError(f"Could not synthesize a verbatim reading of: {text!r}")


def _normalize(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.lower()).strip()


async def _speak_once(text: str, voice: str) -> tuple[bytes, str]:
    audio = bytearray()
    spoken = ""
    headers = {"Authorization": f"Bearer {get_access_token(VOICE_LIVE_SCOPE)}"}
    async with websockets.connect(voice_live_url(), additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "instructions": (
                "You are a text-to-speech engine, not an assistant. Read the text inside <read> tags aloud, "
                "word for word. Never answer it, comment on it or add anything."
            ),
            "voice": {"name": voice, "type": "azure-standard"},
            "tools": [],
            "turn_detection": None,
            "temperature": 0.6,
        }}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": f"<read>{text}</read>"}]}}))
        await ws.send(json.dumps({"type": "response.create"}))
        async for raw in ws:
            event = json.loads(raw)
            if event["type"] in ("response.audio.delta", "response.output_audio.delta"):
                audio.extend(base64.b64decode(event["delta"]))
            elif event["type"] in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
                spoken += event.get("delta", "")
            elif event["type"] == "response.done":
                break
    return bytes(audio), spoken


def silence(ms: int) -> bytes:
    return bytes(SAMPLE_RATE * 2 * ms // 1000)


def cough(ms: int = 300) -> bytes:
    """A synthetic cough-like burst: low tone + noise with a fast decay."""
    n = SAMPLE_RATE * ms // 1000
    t = np.arange(n) / SAMPLE_RATE
    signal = (np.sin(2 * np.pi * 180 * t) * 0.5 + np.random.randn(n) * 0.5) * np.exp(-t * 18)
    return (signal * 0.9 * 32767).clip(-32767, 32767).astype(np.int16).tobytes()
