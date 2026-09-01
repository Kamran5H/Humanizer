import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
font = ImageFont.truetype("arial.ttf", 60)

# Method 1: Old Fullscreen RGBA Alpha Composite
t0 = time.time()
for _ in range(100):
    base = Image.fromarray(frame).convert("RGBA")
    sub = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(sub)
    sdraw.text((500, 800), "Hello World Subtitles", font=font, fill=(255, 230, 0, 255), stroke_width=4, stroke_fill=(0, 0, 0))
    base = Image.alpha_composite(base, sub)
    res = np.array(base.convert("RGB"))
t_old = time.time() - t0
print(f"Old Fullscreen RGBA composite: {t_old:.3f}s for 100 frames ({t_old/100*1000:.1f} ms/frame)")

# Method 2: Direct In-Place PIL Draw
t0 = time.time()
for _ in range(100):
    img = Image.fromarray(frame.copy())
    draw = ImageDraw.Draw(img)
    draw.text((500, 800), "Hello World Subtitles", font=font, fill=(255, 230, 0), stroke_width=4, stroke_fill=(0, 0, 0))
    res = np.asarray(img)
t_new = time.time() - t0
print(f"Direct In-Place PIL Draw: {t_new:.3f}s for 100 frames ({t_new/100*1000:.1f} ms/frame)")

print(f"Speedup: {t_old / t_new:.2f}x faster!")
