from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import collector_state_path, read_json, write_json_atomic


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, timeout: float = 70):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "limit": 100,
            "timeout": 0,
            "allowed_updates": ["message", "edited_message"],
        }
        if offset is not None:
            payload["offset"] = offset
        request = urllib.request.Request(
            f"{self.base_url}/getUpdates",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise TelegramError(f"Telegram getUpdates failed: {error}") from error
        if not result.get("ok"):
            raise TelegramError(f"Telegram getUpdates failed: {result}")
        return result["result"]


def collect_daily_updates(
    token: str,
    chat_id: str,
    state_dir: Path,
) -> dict[str, int | str]:
    """Collect pending Bot API updates and append them to daily raw JSONL files."""
    client = TelegramClient(token)
    state_file = collector_state_path(state_dir)
    state = read_json(state_file, {}) or {}
    offset = state.get("offset")
    updates = client.get_updates(offset=offset)
    if not updates:
        return {"updates": 0, "messages": 0, "state": str(state_file)}

    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    raw_dir = state_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(raw_dir, 0o700)
    message_count = 0
    for update in updates:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat = message.get("chat") or {}
        if str(chat.get("id")) != str(chat_id):
            continue
        timestamp = message.get("date", update.get("date"))
        if timestamp is None:
            continue
        day = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        raw_file = raw_dir / f"{day}.jsonl"
        with raw_file.open("a", encoding="utf-8") as output:
            output.write(json.dumps(update, ensure_ascii=False) + "\n")
        os.chmod(raw_file, 0o600)
        message_count += 1

    next_offset = max(update["update_id"] for update in updates) + 1
    write_json_atomic(
        state_file,
        {
            "offset": next_offset,
            "chat_id": str(chat_id),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "updates": len(updates),
        "messages": message_count,
        "state": str(state_file),
    }
