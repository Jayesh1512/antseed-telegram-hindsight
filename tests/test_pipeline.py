from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telegram_hindsight.episodes import build_episodes
from telegram_hindsight.export_reader import read_export
from telegram_hindsight.normalize import normalize_messages
from telegram_hindsight.pipeline import build_document

NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


def raw(
    message_id: int,
    text: str = "hello",
    date: str = "2026-11-25T09:00:00Z",
    reply_to: int | None = None,
    chat_id: str = "-1003701057366",
    topic_id: int | None = None,
    **extra,
):
    value = {
        "id": message_id,
        "type": "message",
        "date": date,
        "from": "Example User",
        "from_id": "user-1",
        "text": text,
        "chat": {"id": int(chat_id)},
    }
    if reply_to is not None:
        value["reply_to_message_id"] = reply_to
    if topic_id is not None:
        value["message_thread_id"] = topic_id
    value.update(extra)
    return value


class PipelineTests(unittest.TestCase):
    def test_sample_export_filters_noise_and_keeps_reply_chain(self):
        source = Path(__file__).parents[1] / "examples" / "sample_export.json"
        result = build_document(
            read_export(source), "-1003701057366", now=NOW, group_title="Partner - Antseed"
        )
        self.assertEqual(result.stats.input_messages, 4)
        self.assertEqual(result.stats.noise_removed, 2)
        self.assertEqual(result.stats.episodes_created, 1)
        self.assertEqual(len(result.episodes[0].messages), 2)
        self.assertIn("Send the requirements on Friday", result.content)
        self.assertNotIn('"gm"', result.content)
        self.assertEqual(
            result.document_id, "telegram:group:-1003701057366:memory"
        )

    def test_duplicate_message_keeps_latest_edit(self):
        original = raw(1, "old text", edit_date=None)
        edited = raw(1, "corrected text", edit_date=1795680000)
        messages, counts = normalize_messages([original, edited])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].text, "corrected text")
        self.assertEqual(counts["duplicates_removed"], 1)

    def test_short_answer_inside_reply_chain_is_kept(self):
        messages, counts = normalize_messages(
            [raw(1, "Can we ship Friday?"), raw(2, "yes", reply_to=1)]
        )
        self.assertEqual(len(messages), 2)
        self.assertEqual(counts["noise_removed"], 0)

    def test_secret_and_optional_contact_redaction(self):
        secret = "Use sk-proj-abcdefghijklmnopqrstuvwxyz123456 and password=hunter2"
        messages, _ = normalize_messages(
            [raw(1, secret), raw(2, "Mail a@example.com or +1 415 555 1212")],
            redact_contact_info=True,
        )
        self.assertNotIn("sk-proj", messages[0].text)
        self.assertNotIn("hunter2", messages[0].text)
        self.assertNotIn("a@example.com", messages[1].text)
        self.assertNotIn("415 555", messages[1].text)

    def test_reply_chain_stays_atomic_across_time_gap_and_topics_split(self):
        values = [
            raw(1, "root", date="2026-11-25T09:00:00Z"),
            raw(2, "answer", date="2026-11-25T10:00:00Z", reply_to=1),
            raw(3, "other topic", date="2026-11-25T10:01:00Z", topic_id=9),
        ]
        messages, _ = normalize_messages(values)
        episodes = build_episodes(messages)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(len(episodes[0].messages), 2)
        all_ids = [message.message_id for episode in episodes for message in episode.messages]
        self.assertEqual(sorted(all_ids), [1, 2, 3])

    def test_message_cap_creates_non_overlapping_episodes(self):
        values = [
            raw(index, f"message {index}", date=f"2026-11-25T09:{index:02d}:00Z")
            for index in range(25)
        ]
        messages, _ = normalize_messages(values)
        episodes = build_episodes(messages, max_messages=20)
        self.assertEqual([len(episode.messages) for episode in episodes], [20, 5])
        ids = [message.message_id for episode in episodes for message in episode.messages]
        self.assertEqual(len(ids), len(set(ids)))

    def test_window_and_chat_filters(self):
        values = [
            raw(1, "current"),
            raw(2, "too old", date="2026-01-01T00:00:00Z"),
            raw(3, "other chat", chat_id="-42"),
        ]
        result = build_document(values, "-1003701057366", now=NOW)
        self.assertEqual(result.stats.outside_window, 1)
        self.assertEqual(result.stats.other_chats, 1)
        self.assertEqual(result.stats.episodes_created, 1)

    def test_hash_is_stable_across_input_order(self):
        values = [raw(2, "second"), raw(1, "first")]
        first = build_document(values, "-1003701057366", now=NOW)
        second = build_document(list(reversed(values)), "-1003701057366", now=NOW)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.document_id, second.document_id)

    def test_desktop_rich_text_is_flattened(self):
        value = raw(1, [{"type": "plain", "text": "hello "}, {"type": "bold", "text": "world"}])
        messages, _ = normalize_messages([value])
        self.assertEqual(messages[0].text, "hello world")


if __name__ == "__main__":
    unittest.main()
