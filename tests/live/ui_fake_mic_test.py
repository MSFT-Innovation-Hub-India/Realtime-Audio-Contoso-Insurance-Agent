"""Browser test of the Chainlit UI with a fake microphone (synthetic speech + a cough).

Starts the browser with a WAV file as the microphone, turns the mic on, and prints the chat as it evolves;
then turns the mic off and asks a typed question. Works against a local run or the deployed app.

Requires `az login`, .env, `pip install -r requirements-dev.txt` and `python -m playwright install chromium`.
Run:  python tests/live/ui_fake_mic_test.py --url http://localhost:8000
      python tests/live/ui_fake_mic_test.py --url https://<app>.azurecontainerapps.io
"""

import argparse
import asyncio
import wave

from playwright.async_api import async_playwright

from _helpers import OUTPUT_DIR, SAMPLE_RATE, cough, silence, synthesize_speech


async def make_fake_microphone() -> str:
    question = await synthesize_speech("Hello, can you tell me the status of my insurance claims please?")
    pcm = silence(1500) + cough() + silence(2500) + question + silence(25000)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "fake_mic.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return str(path)


async def print_chat(page, marker: str):
    body = await page.inner_text("body")
    start = body.find(marker)
    for line in body[max(start, 0):].splitlines():
        if line.strip():
            print("  |", line.strip()[:160])


async def main(url: str, browser_channel: str | None):
    wav = await make_fake_microphone()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=browser_channel, headless=True, args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}%noloop",
            "--autoplay-policy=no-user-gesture-required",
        ])
        page = await (await browser.new_context(permissions=["microphone"])).new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.goto(url)
        await page.wait_for_selector("text=Welcome", timeout=60000)
        await page.wait_for_timeout(3000)

        microphone = page.locator("#upload-button").locator("xpath=preceding::button[1]")
        await microphone.click()
        for i in range(10):
            await page.wait_for_timeout(4000)
            print(f"--- t={4 * (i + 1)}s ---")
            await print_chat(page, "Hi, Welcome")
        await page.screenshot(path=str(OUTPUT_DIR / "voice_ui.png"), full_page=True)

        await microphone.click()  # mic off
        await page.wait_for_timeout(1000)
        await page.get_by_placeholder("Type your message here...").fill("How do I file a claim with Contoso?")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(15000)
        print("--- after typed question (mic off) ---")
        await print_chat(page, "How do I file a claim")

        print("console errors:", len(errors))
        for error in errors[:5]:
            print("  ERR", error[:200])
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--channel", default=None, help="Browser channel, e.g. msedge or chrome (default: bundled chromium)")
    args = parser.parse_args()
    asyncio.run(main(args.url, args.channel))
