from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


INVALID_FILENAME_CHARS = re.compile(r"[\\/:*?\"<>|]")
BOT_FILENAME_SUFFIX = "pelicanaudiobot @PelicanAudioBot"


def sanitize_title(title: str, max_length: int = 40) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("", title).strip()
    return cleaned[:max_length] or "Трек"


def sanitize_filename(title: str, max_length: int = 40) -> str:
    cleaned = sanitize_title(title, max_length=max_length)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Трек"


def build_track_filename(title: str) -> str:
    combined = f"{title} - {BOT_FILENAME_SUFFIX}".strip()
    return sanitize_filename(combined, max_length=120)


def build_auto_title(preset_title: str, brief: str) -> str:
    match = re.search(r"[А-Яа-яЁёA-Za-z0-9]{3,}", brief)
    if match:
        keyword = match.group(0)
    else:
        keyword = "Трек"
    return f"{preset_title} — {keyword}"


def is_valid_title(title: str) -> bool:
    if not title:
        return False
    if INVALID_FILENAME_CHARS.search(title):
        return False
    return 1 <= len(title.strip()) <= 40


def ensure_storage_dir(storage_dir: Path) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)
