import time
import requests

prompts = ["a serene mountain landscape at sunrise, 8k cinematic lighting"]

for m in ["flux", "turbo", None]:
    for (w, h) in [(1280, 720), (1920, 1080)]:
        m_str = f"&model={m}" if m else ""
        url = f"https://image.pollinations.ai/prompt/serene%20mountain%20landscape%20at%20sunrise?width={w}&height={h}{m_str}&nologo=true&seed=42"
        t0 = time.time()
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            print(f"Pollinations model={m} {w}x{h} -> Status: {r.status_code}, Size: {len(r.content)} bytes in {time.time()-t0:.2f}s")
        except Exception as e:
            print(f"Pollinations model={m} {w}x{h} -> FAILED in {time.time()-t0:.2f}s: {type(e).__name__} {e}")
