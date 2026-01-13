from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PRESETS_PATH = Path(__file__).resolve().parent / "presets.yaml"


def load_presets() -> list[dict[str, Any]]:
    with PRESETS_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data.get("presets", [])


def get_preset(preset_id: str) -> dict[str, Any] | None:
    for preset in load_presets():
        if preset["id"] == preset_id:
            return preset
    return None
