import time
import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

models = ["sana", "turbo", "flux", None]

for m in models:
    m_param = f"&model={m}" if m else ""
    url = f"https://image.pollinations.ai/prompt/crystal%20clear%20waterfall%20in%20a%20lush%20forest?width=1280&height=720{m_param}&nologo=true&seed=12345"
    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, timeout=20)
        print(f"Model [{m}]: Status {r.status_code}, Content-Type: {r.headers.get('Content-Type')}, Size: {len(r.content)} bytes in {time.time()-t0:.2f}s (Used: {r.headers.get('x-model-used')})")
    except Exception as e:
        print(f"Model [{m}]: FAILED in {time.time()-t0:.2f}s: {e}")
