from __future__ import annotations

from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "presets" / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_grok_messages(system_text: str, user_text: str) -> list[dict]:
    return [
        {"role": "system", "content": [{"type": "text", "text": system_text}]},
        {"role": "user", "content": [{"type": "text", "text": user_text}]},
    ]


def _render_template(template: str, **kwargs: str) -> str:
    return template.format(**kwargs)


def build_lyrics_messages(preset: dict, brief: str) -> list[dict]:
    system_text = _load_prompt("grok_lyrics_system_ru.txt")
    user_template = _load_prompt("grok_lyrics_user_template.txt")
    user_text = _render_template(
        user_template,
        preset_title=preset["title"],
        preset_description=preset["description"],
        category_title=preset.get("category_title", ""),
        mood=preset["hints"]["mood"],
        vibe=preset["hints"]["vibe"],
        genre=preset["hints"]["genre"],
        short_form=str(preset["short_form"]).lower(),
        recommendations=preset.get("recommendations", ""),
        brief=brief,
    )
    return _build_grok_messages(system_text, user_text)


def build_tags_messages(preset: dict, content: str, mode: str) -> list[dict]:
    system_text = _load_prompt("grok_tags_system_ru.txt")
    user_template = _load_prompt("grok_tags_user_template.txt")
    user_text = _render_template(
        user_template,
        preset_title=preset["title"],
        preset_description=preset["description"],
        category_title=preset.get("category_title", ""),
        mood=preset["hints"]["mood"],
        vibe=preset["hints"]["vibe"],
        genre=preset["hints"]["genre"],
        short_form=str(preset["short_form"]).lower(),
        mode=mode,
        content=content,
    )
    return _build_grok_messages(system_text, user_text)


def build_edit_messages(lyrics: str, edit_request: str) -> list[dict]:
    system_text = _load_prompt("grok_edit_system_ru.txt")
    user_template = _load_prompt("grok_edit_user_template.txt")
    user_text = _render_template(user_template, lyrics=lyrics, edit_request=edit_request)
    return _build_grok_messages(system_text, user_text)


def build_instrumental_messages(preset: dict, brief: str) -> list[dict]:
    system_text = _load_prompt("grok_instrumental_system_ru.txt")
    user_template = _load_prompt("grok_instrumental_user_template.txt")
    user_text = _render_template(
        user_template,
        preset_title=preset["title"],
        preset_description=preset["description"],
        category_title=preset.get("category_title", ""),
        mood=preset["hints"]["mood"],
        vibe=preset["hints"]["vibe"],
        genre=preset["hints"]["genre"],
        brief=brief,
    )
    return _build_grok_messages(system_text, user_text)


def build_user_lyrics_messages(preset: dict, brief: str, user_lyrics_raw: str) -> list[dict]:
    system_text = _load_prompt("grok_user_lyrics_system_ru.txt")
    user_template = _load_prompt("grok_user_lyrics_user_template.txt")
    user_text = _render_template(
        user_template,
        preset_title=preset["title"],
        preset_description=preset["description"],
        category_title=preset.get("category_title", ""),
        mood=preset["hints"]["mood"],
        vibe=preset["hints"]["vibe"],
        genre=preset["hints"]["genre"],
        brief=brief,
        user_lyrics_raw=user_lyrics_raw,
    )
    return _build_grok_messages(system_text, user_text)
