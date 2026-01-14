from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PRESETS_PATH = Path(__file__).resolve().parent / "presets.yaml"


def load_presets() -> list[dict[str, Any]]:
    with PRESETS_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    categories = {cat["id"]: cat for cat in data.get("categories", [])}
    presets: list[dict[str, Any]] = []
    for preset in data.get("presets", []):
        preset = dict(preset)
        category = categories.get(preset.get("category_id"))
        if category and "category_title" not in preset:
            preset["category_title"] = category.get("title")
        presets.append(preset)
    return presets


def load_categories() -> list[dict[str, Any]]:
    with PRESETS_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data.get("categories", [])


def get_presets_by_category(category_id: str) -> list[dict[str, Any]]:
    return [preset for preset in load_presets() if preset.get("category_id") == category_id]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    for preset in load_presets():
        if preset["id"] == preset_id:
            return preset
    return None


def get_starter_preset() -> dict[str, Any] | None:
    for preset in load_presets():
        if preset.get("starter"):
            return preset
    return None
