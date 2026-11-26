from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Message:
    chat_id: str
    message_id: int
    sender_id: str
    sender_name: str
    sent_at: datetime
    text: str
    topic_id: str = "0"
    reply_to_id: int | None = None
    edited_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "topic_id": self.topic_id,
            "reply_to_id": self.reply_to_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "sent_at": self.sent_at.isoformat().replace("+00:00", "Z"),
            "text": self.text,
        }
        if self.edited_at:
            result["edited_at"] = self.edited_at.isoformat().replace("+00:00", "Z")
        return result


@dataclass(frozen=True)
class Episode:
    chat_id: str
    topic_id: str
    episode_id: str
    messages: tuple[Message, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "chat_id": self.chat_id,
            "topic_id": self.topic_id,
            "started_at": self.messages[0].sent_at.isoformat().replace("+00:00", "Z"),
            "ended_at": self.messages[-1].sent_at.isoformat().replace("+00:00", "Z"),
            "messages": [message.as_dict() for message in self.messages],
        }


@dataclass
class BuildStats:
    input_messages: int = 0
    normalized_messages: int = 0
    duplicates_removed: int = 0
    noise_removed: int = 0
    outside_window: int = 0
    other_chats: int = 0
    invalid_messages: int = 0
    episodes_created: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "input_messages": self.input_messages,
            "normalized_messages": self.normalized_messages,
            "duplicates_removed": self.duplicates_removed,
            "noise_removed": self.noise_removed,
            "outside_window": self.outside_window,
            "other_chats": self.other_chats,
            "invalid_messages": self.invalid_messages,
            "episodes_created": self.episodes_created,
        }


@dataclass
class BuildResult:
    document_id: str
    content: str
    content_hash: str
    timestamp: str
    context: str
    tags: list[str]
    observation_scopes: list[list[str]]
    metadata: dict[str, str]
    stats: BuildStats
    episodes: list[Episode] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "content_hash": self.content_hash,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "stats": self.stats.as_dict(),
        }
