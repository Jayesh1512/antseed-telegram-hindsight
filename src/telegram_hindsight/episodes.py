from __future__ import annotations

from collections import defaultdict

from .models import Episode, Message


def build_episodes(
    messages: list[Message],
    gap_seconds: int = 1800,
    max_messages: int = 20,
    max_chars: int = 16000,
) -> list[Episode]:
    """Build non-overlapping episodes while keeping reply chains atomic."""
    if not messages:
        return []

    by_scope: dict[tuple[str, str], list[Message]] = defaultdict(list)
    for message in messages:
        by_scope[(message.chat_id, message.topic_id)].append(message)

    episodes: list[Episode] = []
    for (chat_id, topic_id), scoped in by_scope.items():
        scoped = sorted(scoped, key=lambda item: (item.sent_at, item.message_id))
        by_id = {message.message_id: message for message in scoped}
        parent: dict[int, int] = {message.message_id: message.message_id for message in scoped}

        def find(message_id: int) -> int:
            while parent[message_id] != message_id:
                parent[message_id] = parent[parent[message_id]]
                message_id = parent[message_id]
            return message_id

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for message in scoped:
            if message.reply_to_id in by_id:
                union(message.message_id, message.reply_to_id)  # type: ignore[arg-type]

        chains: dict[int, list[Message]] = defaultdict(list)
        for message in scoped:
            chains[find(message.message_id)].append(message)
        units = sorted(
            [sorted(chain, key=lambda item: (item.sent_at, item.message_id)) for chain in chains.values()],
            key=lambda chain: (chain[0].sent_at, chain[0].message_id),
        )

        packed: list[list[Message]] = []
        current: list[Message] = []
        current_chars = 0
        for unit in units:
            unit_chars = sum(len(message.text) for message in unit)
            gap = (
                (unit[0].sent_at - current[-1].sent_at).total_seconds()
                if current
                else 0
            )
            should_split = bool(current) and (
                gap > gap_seconds
                or len(current) + len(unit) > max_messages
                or current_chars + unit_chars > max_chars
            )
            if should_split:
                packed.append(current)
                current = []
                current_chars = 0
            current.extend(unit)
            current_chars += unit_chars
        if current:
            packed.append(current)

        for chain in packed:
            episodes.append(
                Episode(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    episode_id=f"{chat_id}:{topic_id}:{chain[0].message_id}",
                    messages=tuple(chain),
                )
            )

    return sorted(
        episodes,
        key=lambda episode: (
            episode.messages[0].sent_at,
            episode.chat_id,
            episode.topic_id,
            episode.episode_id,
        ),
    )
