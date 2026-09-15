"""
utils/snapshot.py
Extracts a single JPEG snapshot/thumbnail from a downloaded video using FFmpeg.
"""

import asyncio
import logging
import os
import uuid

logger = logging.getLogger("V.snapshot")


async def extract_snapshot(video_path: str, output_dir: str, timestamp: str = "00:00:02") -> str:
    """
    Extracts a single frame from `video_path` at `timestamp` and saves it as JPEG.
    Returns the path to the generated snapshot file.
    Raises RuntimeError if ffmpeg fails.
    """
    output_path = os.path.join(output_dir, f"snap_{uuid.uuid4().hex}.jpg")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", timestamp,
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0 or not os.path.exists(output_path):
        error_msg = stderr.decode(errors="ignore")[-500:]
        raise RuntimeError(f"FFmpeg snapshot extraction failed: {error_msg}")

    logger.info(f"Snapshot extracted: {output_path}")
    return output_path


def safe_remove(*paths: str) -> None:
    """Best-effort cleanup of temp files."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as e:
            logger.warning(f"Could not remove temp file {path}: {e}")
