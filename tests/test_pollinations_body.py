import requests

url = "https://image.pollinations.ai/prompt/serene%20mountain?width=1280&height=720&nologo=true"
r = requests.get(url, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
})
print("STATUS:", r.status_code)
print("HEADERS:", dict(r.headers))
print("BODY:", r.text[:500])
