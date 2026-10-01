"""Live check that the assistant uses the persona's grammatical gender when speaking Hindi.

Users usually address the assistant with the default polite form ("आप ... कर सकते हैं"); the model must
still answer about itself with the persona's gender (female by default: "मैं कर सकती हूँ").

Requires `az login` and .env.  Run:  python tests/live/hindi_gender_check.py
"""

import asyncio
import re
import time

import _helpers  # noqa: F401  (sets up sys.path / working directory)
from app import system_prompt
from envconfig import DefaultConfig
from voicelive_client import VoiceLiveModelClient

QUESTIONS = [
    "Hello, can you help me with my insurance?",
    "क्या आप हिंदी में बात कर सकते हैं? मुझे अपने इंश्योरेंस क्लेम का स्टेटस जानना है।",
    "आप अभी क्या कर रहे हैं? और क्या आप मेरी शिकायत दर्ज कर पाएँगे?",
    "ठीक है। आप मुझे कैसे मदद करेंगे? अपने बारे में थोड़ा बताइए, आप क्या-क्या कर सकते हैं?",
    "Contoso कौन-कौन से हेल्थ इंश्योरेंस प्लान देता है? हिंदी में बताइए।",
]
# First-person forms only: "हूँ" is exclusively first-person singular ("मैं कर सकता/सकती हूँ"),
# and the "-ऊँगा/-ऊँगी" future is always first person ("मैं बताऊँगा/बताऊँगी").
MASC = re.compile(r"\S+ा\s+(?:हूँ|हूं)|\S+(?:ूँगा|ूंगा)")
FEM = re.compile(r"\S+ी\s+(?:हूँ|हूं)|\S+(?:ूँगी|ूंगी)")


async def main():
    client = VoiceLiveModelClient(system_prompt=system_prompt)
    texts: dict[str, str] = {}
    client.on("conversation.text.delta", lambda e: texts.__setitem__(e["item_id"], texts.get(e["item_id"], "") + e["transcript"]))
    await client.connect()
    await asyncio.sleep(1.5)
    masculine = feminine = 0
    for question in QUESTIONS:
        before = set(texts)
        await client.send_user_message_content([{"type": "input_text", "text": question}])
        end = time.time() + 45
        while time.time() < end:
            await asyncio.sleep(0.5)
            if client._response_idle.is_set() and not client._followup_pending and set(texts) - before:
                await asyncio.sleep(1.5)
                if client._response_idle.is_set():
                    break
        reply = " ".join(texts[k] for k in texts if k not in before)
        m, f = MASC.findall(reply), FEM.findall(reply)
        masculine += len(m)
        feminine += len(f)
        print(f"\nQ: {question}\nA: {reply}\n   first-person masculine={m} feminine={f}")
    await client.disconnect()

    expected_female = DefaultConfig.assistant_gender == "female"
    wrong = masculine if expected_female else feminine
    print(f"\nSUMMARY persona={DefaultConfig.assistant_gender} masculine={masculine} feminine={feminine}")
    print("RESULT:", "PASS" if wrong == 0 else "FAIL")
    raise SystemExit(0 if wrong == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
