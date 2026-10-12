"""Bounded local FFmpeg fallback for genuine HEIC and DNG photo previews.
Author: @dvkrum

Source photos remain read-only. Mislabelled ZIP files with .webp suffix are
not pictures, and are deliberately excluded from this fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile

from photo_config import STATE_DIR

MAX_PREVIEW_BYTES = 32 * 1024 * 1024
FFMPEG_SECONDS = 25
PREVIEW_EDGE = 1024


def supported_fallback(path: Path) -> bool:
    """Validate actual file signature, not only its extension."""
    if path.suffix.lower() not in {".heic", ".dng"}:
        return False
    try:
        with path.open("rb") as stream:
            signature = stream.read(16)
    except OSError:
        return False
    if path.suffix.lower() == ".heic":
        return len(signature) >= 12 and signature[4:8] == b"ftyp"
    return signature[:4] in {b"II*\x00", b"MM\x00*"}


@contextmanager
def open_image(image_module, path):
    """Open normally, or decode a bounded private preview for HEIC/DNG."""
    from PIL import UnidentifiedImageError

    path = Path(path)
    try:
        src = image_module.open(path)
    except UnidentifiedImageError:
        if not supported_fallback(path):
            raise
    else:
        with src:
            yield src
        return

    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".decode-preview-", dir=STATE_DIR) as directory:
        preview = Path(directory) / "preview.png"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-threads", "2", "-i", str(path), "-map", "0:v:0",
            "-frames:v", "1", "-vf",
            f"scale={PREVIEW_EDGE}:{PREVIEW_EDGE}:force_original_aspect_ratio=decrease",
            "-c:v", "png", "-f", "image2", "-y", str(preview)
        ]
        try:
            done = subprocess.run(
                command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=FFMPEG_SECONDS, check=False,
            )
            if done.returncode != 0 or not preview.is_file():
                raise UnidentifiedImageError("bounded FFmpeg preview failed")
            if not 0 < preview.stat().st_size <= MAX_PREVIEW_BYTES:
                raise UnidentifiedImageError("bounded FFmpeg preview invalid size")
            with image_module.open(preview) as decoded:
                decoded.load()
                yield decoded
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            raise UnidentifiedImageError("bounded FFmpeg preview unavailable") from exc
