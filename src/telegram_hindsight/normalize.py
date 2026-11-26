from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .models import Message

TRIVIAL_MESSAGES = {
    "gm",
    "good morning",
    "hi",
    "hello",
    "hey",
    "ok",
    "okay",
    "thanks",
    "thank you",
    "thx",
    "lol",
    "+1",
    "👍",
    "🙏",
}

SECRET_PATTERNS = [
    re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s]+", re.IGNORECASE),
    re.compile(r"(?i)\b(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*([^\s,;]+)"),
]
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")


def flatten_text(value: Any) -> str:
    """Flatten Telegram Desktop rich-text arrays without rewriting content."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_text(part) for part in value)
    if isinstance(value, dict):
        return flatten_text(value.get("text", ""))
    return str(value)


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        result = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str) and value.isdigit():
        result = datetime.fromtimestamp(int(value), tz=timezone.utc)
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _chat_id(raw: dict[str, Any], fallback_chat_id: str | None) -> str | None:
    chat = raw.get("chat")
    if isinstance(chat, dict) and chat.get("id") is not None:
        return str(chat["id"])
    for key in ("chat_id", "peer_id"):
        if raw.get(key) is not None:
            return str(raw[key])
    return fallback_chat_id


def is_noise(raw: dict[str, Any], text: str, reply_to_id: int | None) -> bool:
    if raw.get("type") == "service":
        return True
    service_keys = {
        "new_chat_members",
        "left_chat_member",
        "new_chat_title",
        "new_chat_photo",
        "delete_chat_photo",
        "group_chat_created",
        "pinned_message",
    }
    if any(raw.get(key) is not None for key in service_keys):
        return True
    if raw.get("media_type") == "sticker" or raw.get("sticker_emoji"):
        return not text.strip()
    if text.strip().startswith("/"):
        return True
    if reply_to_id is None and text.strip().lower() in TRIVIAL_MESSAGES:
        return True
    return not text.strip()


def redact(text: str, include_contact_info: bool = False) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)"):
            result = pattern.sub(
                lambda match: f"{match.group(1)}=[REDACTED:secret]", result
            )
        else:
            result = pattern.sub("[REDACTED:secret]", result)
    if include_contact_info:
        result = EMAIL_PATTERN.sub("[REDACTED:email]", result)
        result = PHONE_PATTERN.sub("[REDACTED:phone]", result)
    return result


def normalize_message(
    raw: dict[str, Any],
    fallback_chat_id: str | None = None,
    redact_contact_info: bool = False,
) -> tuple[Message | None, str | None]:
    """Return (message, rejection). Rejection is noise, invalid, or None."""
    chat_id = _chat_id(raw, fallback_chat_id)
    message_id = raw.get("message_id", raw.get("id"))
    sent_at = parse_time(
        raw.get("sent_at", raw.get("date", raw.get("date_unixtime")))
    )
    if chat_id is None or message_id is None or sent_at is None:
        return None, "invalid"
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return None, "invalid"

    reply_to = raw.get("reply_to_id", raw.get("reply_to_message_id"))
    try:
        reply_to = int(reply_to) if reply_to is not None else None
    except (TypeError, ValueError):
        reply_to = None

    text = flatten_text(raw.get("text", raw.get("caption", "")))
    if is_noise(raw, text, reply_to):
        return None, "noise"

    raw_from = raw.get("from")
    sender_name = flatten_text(
        raw.get("sender_name", raw_from if raw_from is not None else raw.get("sender", "unknown"))
    )
    if isinstance(raw_from, dict):
        sender_name = " ".join(
            part
            for part in [
                raw_from.get("first_name", ""),
                raw_from.get("last_name", ""),
            ]
            if part
        ) or raw_from.get("username", "unknown")
    sender_id = str(
        raw.get("sender_id")
        or raw.get("from_id")
        or (raw_from.get("id") if isinstance(raw_from, dict) else None)
        or (raw.get("sender_chat") or {}).get("id")
        or "unknown"
    )
    raw_topic_id = raw.get("topic_id")
    if raw_topic_id is None:
        raw_topic_id = raw.get("message_thread_id", 0)
    topic_id = str(raw_topic_id or 0)
    edited_at = parse_time(raw.get("edited_at", raw.get("edit_date")))

    return (
        Message(
            chat_id=chat_id,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name or "unknown",
            sent_at=sent_at,
            text=redact(text.strip(), redact_contact_info).strip(),
            topic_id=topic_id,
            reply_to_id=reply_to,
            edited_at=edited_at,
        ),
        None,
    )


def normalize_messages(
    raw_messages: list[dict[str, Any]],
    fallback_chat_id: str | None = None,
    redact_contact_info: bool = False,
) -> tuple[list[Message], dict[str, int]]:
    messages: dict[tuple[str, int], Message] = {}
    counts = {
        "input_messages": len(raw_messages),
        "duplicates_removed": 0,
        "noise_removed": 0,
        "invalid_messages": 0,
    }
    for raw in raw_messages:
        message, rejection = normalize_message(
            raw, fallback_chat_id, redact_contact_info
        )
        if rejection == "invalid":
            counts["invalid_messages"] += 1
            continue
        if rejection == "noise":
            counts["noise_removed"] += 1
            continue
        if rejection is not None or message is None:
            continue
        key = (message.chat_id, message.message_id)
        previous = messages.get(key)
        if previous is not None:
            counts["duplicates_removed"] += 1
            previous_edit = previous.edited_at or previous.sent_at
            current_edit = message.edited_at or message.sent_at
            if previous_edit > current_edit:
                continue
        messages[key] = message
    counts["normalized_messages"] = len(messages)
    return sorted(messages.values(), key=lambda item: (item.sent_at, item.message_id)), counts
