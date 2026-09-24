import time
import subprocess
import shutil
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

def test_fast_morph(frames, out_path, duration, output_size=(1920, 1080), fps=30):
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = output_size

    n = len(frames)
    if n == 1:
        # Single frame video with smooth zoompan
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(frames[0]),
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=yuv420p",
            "-t", f"{duration:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps),
            str(out)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return out if res.returncode == 0 and out.exists() else None

    # Multi-frame fast smooth sequence
    frame_dur = max(0.5, float(duration) / n)
    stage = Path(tempfile.mkdtemp(prefix="fast_seq_"))
    try:
        concat_txt = stage / "concat.txt"
        with open(concat_txt, "w") as f:
            for fr in frames:
                f.write(f"file '{Path(fr).as_posix()}'\nduration {frame_dur:.3f}\n")
            f.write(f"file '{Path(frames[-1]).as_posix()}'\n")

        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=yuv420p",
            "-t", f"{duration:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps),
            str(out)
        ]
        t0 = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        print(f"Fast morph generated in {time.time()-t0:.2f}s, size: {out.stat().st_size if out.exists() else 0} bytes (rc={res.returncode})")
        if res.returncode == 0 and out.exists() and out.stat().st_size > 1024:
            return out
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return None

# Test with 4 frames
tmp = Path(tempfile.mkdtemp(prefix="test_frames_"))
frames = []
for i, col in enumerate([(20, 40, 80), (80, 40, 20), (20, 80, 40), (80, 20, 80)]):
    p = tmp / f"f_{i}.jpg"
    im = Image.new("RGB", (1920, 1080), col)
    im.save(p)
    frames.append(p)

out = tmp / "morph_test.mp4"
res = test_fast_morph(frames, out, duration=4.0)
print("Morph result:", res)
shutil.rmtree(tmp, ignore_errors=True)
