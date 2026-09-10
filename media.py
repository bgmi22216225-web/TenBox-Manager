"""
media.py
FFmpeg-based video snapshot extraction, and helpers to pull a URL out of the
processing bot's reply text.
"""

import asyncio
import logging
import os
import re
import uuid

from config import WORK_DIR

logger = logging.getLogger("media")

URL_REGEX = re.compile(r"https?://\S+")


def extract_first_url(text: str) -> str | None:
    """Pull the first http(s) URL out of the processing bot's reply."""
    if not text:
        return None
    match = URL_REGEX.search(text)
    if not match:
        return None
    # Trim common trailing punctuation that isn't part of the URL.
    return match.group(0).rstrip(").,>]\"'")


def new_paths(prefix: str) -> tuple[str, str]:
    """Returns (video_path, snapshot_path) inside WORK_DIR for a given job prefix."""
    uid = uuid.uuid4().hex[:8]
    video_path = os.path.join(WORK_DIR, f"{prefix}_{uid}.mp4")
    snapshot_path = os.path.join(WORK_DIR, f"{prefix}_{uid}.jpg")
    return video_path, snapshot_path


async def capture_snapshot(video_path: str, snapshot_path: str, timestamp: str = "00:00:01") -> bool:
    """
    Extract a single sharp frame from video_path at `timestamp` using ffmpeg.
    Returns True on success.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", timestamp,
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        snapshot_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0 or not os.path.exists(snapshot_path):
        logger.error(f"ffmpeg snapshot failed for {video_path}: {stderr.decode(errors='ignore')[-500:]}")
        return False
    return True


def cleanup_files(*paths: str):
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as e:
            logger.warning(f"Failed to delete {path}: {e}")
