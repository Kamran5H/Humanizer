import subprocess
import shutil
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

tmp = Path(tempfile.mkdtemp(prefix="test_working_"))
f1 = tmp / "m_0.jpg"
f2 = tmp / "m_1.jpg"
f3 = tmp / "m_2.jpg"

for i, (col, fp) in enumerate([((30, 80, 180), f1), ((180, 50, 80), f2), ((50, 180, 80), f3)]):
    im = Image.new("RGB", (1280, 720), col)
    d = ImageDraw.Draw(im)
    d.text((100, 100), f"Frame {i}", fill=(255, 255, 255))
    im.save(fp)

import imageio_ffmpeg
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
print(f"FFmpeg binary: {ffmpeg}")

out_mp4 = tmp / "valid_video.mp4"

# Let's test standard ffmpeg input sequence with concat demuxer vs image pattern
concat_file = tmp / "inputs.txt"
with open(concat_file, "w") as f:
    f.write(f"file '{f1.as_posix()}'\nduration 1.5\n")
    f.write(f"file '{f2.as_posix()}'\nduration 1.5\n")
    f.write(f"file '{f3.as_posix()}'\nduration 1.5\n")
    f.write(f"file '{f3.as_posix()}'\n")

cmd = [
    ffmpeg, "-y", "-hide_banner", "-loglevel", "info",
    "-f", "concat", "-safe", "0", "-i", str(concat_file),
    "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
    str(out_mp4)
]

p = subprocess.run(cmd, capture_output=True, text=True)
print("Returncode:", p.returncode)
print("Output size:", out_mp4.stat().st_size if out_mp4.exists() else 0)
if p.stderr:
    print("Stderr tail:\n" + "\n".join(p.stderr.splitlines()[-10:]))

shutil.rmtree(tmp, ignore_errors=True)
