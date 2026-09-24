import time
import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

p_long = "VEO 3 master shot of From towering mountains to crystal-clear waters, the earth continues to amaze us., wide 16:9 cinematic framing, shot on 35mm lens, golden hour volumetric lighting, intricate depth of field, photorealistic detail, 8k resolution, masterpiece color grading, smooth motion, high dynamic range, masterpiece color grading"
p_short = "towering mountains, crystal clear waters, golden hour cinematic lighting, 8k"

for name, p in [("Short Prompt", p_short), ("Long Prompt", p_long)]:
    enc = requests.utils.quote(p)
    url = f"https://image.pollinations.ai/prompt/{enc}?width=1280&height=720&nologo=true&seed=42"
    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, timeout=12)
        print(f"[{name}] Status: {r.status_code}, Size: {len(r.content)} bytes in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[{name}] FAILED in {time.time()-t0:.2f}s: {e}")
