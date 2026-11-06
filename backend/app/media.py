"""Media helpers: frame-length rules, ffprobe inspection, thumbnails, hashing."""

import asyncio
import hashlib
import json
from pathlib import Path

FPS = 24
FRAME_STEP = 8
MIN_FRAMES, MAX_FRAMES = 17, 481


def snap_frames(frames: int) -> int:
    """Nearest valid WanGP/LTX-2 length: 8n+1 frames within the model's limits."""
    frames = max(MIN_FRAMES, min(MAX_FRAMES, int(frames)))
    return round((frames - 1) / FRAME_STEP) * FRAME_STEP + 1


def seconds_to_frames(seconds: float) -> int:
    return snap_frames(round(seconds * FPS) + 1)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def _run(*cmd) -> bytes:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {err.decode(errors='replace')[-300:]}")
    return out


async def probe(path: Path) -> dict:
    out = await _run("ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path))
    info = json.loads(out)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else None
    return {
        "width": video.get("width"), "height": video.get("height"), "fps": fps,
        "frames": int(video["nb_frames"]) if video.get("nb_frames", "").isdigit() else None,
        "duration_s": float(info.get("format", {}).get("duration") or 0) or None,
        "has_audio": has_audio,
    }


async def thumbnail(video: Path, dest: Path, at_s: float = 1.0):
    await _run("ffmpeg", "-y", "-v", "error", "-ss", str(at_s), "-i", str(video),
               "-frames:v", "1", "-vf", "scale=480:-2", str(dest))
