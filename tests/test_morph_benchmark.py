import time
import subprocess
import shutil
import tempfile
from pathlib import Path
from PIL import Image

tmp = Path(tempfile.mkdtemp(prefix="test_morph_"))
f1 = tmp / "frame_00.jpg"
f2 = tmp / "frame_01.jpg"

# Create two test frames
im1 = Image.new("RGB", (1280, 720), (50, 100, 200))
im2 = Image.new("RGB", (1280, 720), (200, 100, 50))
im1.save(f1)
im2.save(f2)

ffmpeg = r"C:\Users\chkam\AppData\Local\Programs\Python\Python314\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win64-v4.2.2.exe"
if not Path(ffmpeg).exists():
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

out_mci = tmp / "out_mci.mp4"
out_blend = tmp / "out_blend.mp4"

print(f"Using ffmpeg: {ffmpeg}")

# Test 1: minterpolate mci
print("\n--- Testing minterpolate MCI (Optical flow) for 3 seconds ---")
t0 = time.time()
vf_mci = "scale=1280:720,minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=none,format=yuv420p"
cmd_mci = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-framerate", "0.66", "-start_number", "0",
           "-i", str(tmp / "frame_%02d.jpg"),
           "-vf", vf_mci, "-t", "3.0",
           "-c:v", "libx264", "-preset", "ultrafast", str(out_mci)]
try:
    p = subprocess.run(cmd_mci, capture_output=True, text=True, timeout=30)
    print(f"MCI finished in {time.time()-t0:.2f}s, size: {out_mci.stat().st_size if out_mci.exists() else 0} bytes")
except subprocess.TimeoutExpired:
    print(f"MCI TIMED OUT (>30s) for a single 3-second scene!")

# Test 2: Fast smooth crossfade / blend / cinematic motion
print("\n--- Testing Fast Smooth Interpolation / Blend ---")
t0 = time.time()
vf_blend = "scale=1280:720,minterpolate=fps=30:mi_mode=blend,format=yuv420p"
cmd_blend = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-framerate", "0.66", "-start_number", "0",
             "-i", str(tmp / "frame_%02d.jpg"),
             "-vf", vf_blend, "-t", "3.0",
             "-c:v", "libx264", "-preset", "ultrafast", str(out_blend)]
try:
    p = subprocess.run(cmd_blend, capture_output=True, text=True, timeout=30)
    print(f"Blend finished in {time.time()-t0:.2f}s, size: {out_blend.stat().st_size if out_blend.exists() else 0} bytes")
except Exception as e:
    print(f"Blend failed: {e}")

shutil.rmtree(tmp, ignore_errors=True)
