from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def _unwrap_message(value: dict[str, Any]) -> dict[str, Any]:
    """Accept Telegram updates, Desktop export messages, and normalized records."""
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def read_export(path: str | Path) -> list[dict[str, Any]]:
    """Read Telegram Desktop JSON or newline-delimited JSON records."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".jsonl":
        values: Iterable[Any] = (
            json.loads(line) for line in text.splitlines() if line.strip()
        )
    else:
        value = json.loads(text)
        if isinstance(value, list):
            values = value
        elif isinstance(value, dict) and isinstance(value.get("messages"), list):
            values = value["messages"]
        elif isinstance(value, dict):
            values = [value]
        else:
            raise ValueError(f"Unsupported Telegram export: {source}")

    messages: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        message = _unwrap_message(value)
        message = dict(message)
        if isinstance(value.get("chat"), dict):
            message.setdefault("chat", value["chat"])
        messages.append(message)
    return messages


def read_inputs(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for path in paths:
        messages.extend(read_export(path))
    return messages


def discover_inputs(raw_dir: str | Path) -> list[Path]:
    root = Path(raw_dir)
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    )
