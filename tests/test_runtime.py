from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from telegram_hindsight import cli
from telegram_hindsight.telegram import collect_daily_updates


class FakeHindsight:
    instances = []

    def __init__(self, base_url, api_key):
        self.base_url = base_url
        self.api_key = api_key
        self.replaced = 0
        self.consolidated = 0
        FakeHindsight.instances.append(self)

    def get_document(self, bank, document_id):
        return None

    def replace_document(self, bank, result):
        self.replaced += 1
        return {"success": True}

    def consolidate(self, bank, scopes):
        self.consolidated += 1
        return {"operation_id": "op-1"}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "export.json"
        self.source.write_text(
            json.dumps(
                {
                    "messages": [
                        {
                            "id": 1,
                            "type": "message",
                            "date": "2026-11-25T09:00:00Z",
                            "from": "Alice",
                            "from_id": "user-1",
                            "text": "Send the requirements on Friday.",
                            "chat": {"id": -1003701057366},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        FakeHindsight.instances = []

    def tearDown(self):
        self.temp.cleanup()

    def args(self, *extra):
        return [
            "sync",
            "--chat-id",
            "-1003701057366",
            "--input",
            str(self.source),
            "--state-dir",
            str(self.root / "state"),
            "--now",
            "2026-11-26T12:00:00Z",
            "--api-key",
            "test-key",
            *extra,
        ]

    def test_checkpoint_skips_unchanged_replay(self):
        with patch.object(cli, "HindsightClient", FakeHindsight):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(self.args()), 0)
                first = json.loads(output.getvalue())
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(self.args()), 0)
                second = json.loads(output.getvalue())
        self.assertEqual(first["status"], "replaced")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(FakeHindsight.instances[0].replaced, 1)
        self.assertEqual(len(FakeHindsight.instances), 1)

    def test_bank_switch_does_not_skip_write(self):
        with patch.object(cli, "HindsightClient", FakeHindsight):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(self.args()), 0)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(self.args("--bank", "hermes")), 0)
                second = json.loads(output.getvalue())
        self.assertEqual(second["status"], "replaced")
        self.assertEqual(len(FakeHindsight.instances), 2)
        self.assertEqual(FakeHindsight.instances[1].replaced, 1)

    def test_empty_sync_fails_closed(self):
        empty = self.root / "empty.json"
        empty.write_text('{"messages": []}', encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            code = cli.main(
                self.args("--input", str(empty), "--api-key", "test-key")
            )
        self.assertEqual(code, 2)
        self.assertEqual(FakeHindsight.instances, [])

    def test_dry_run_does_not_need_api_key(self):
        with redirect_stdout(io.StringIO()) as output:
            code = cli.main(self.args("--api-key", "", "--dry-run"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "dry-run")

    def test_collect_filters_chat_and_saves_offset(self):
        updates = [
            {
                "update_id": 10,
                "message": {
                    "message_id": 1,
                    "date": 1764160000,
                    "chat": {"id": -1003701057366},
                    "text": "keep",
                },
            },
            {
                "update_id": 11,
                "message": {
                    "message_id": 2,
                    "date": 1764160000,
                    "chat": {"id": -42},
                    "text": "drop",
                },
            },
        ]
        with patch("telegram_hindsight.telegram.TelegramClient.get_updates", return_value=updates):
            result = collect_daily_updates("token", "-1003701057366", self.root)
        self.assertEqual(result["messages"], 1)
        state = json.loads((self.root / "collector-state.json").read_text())
        self.assertEqual(state["offset"], 12)
        raw_files = list((self.root / "raw").glob("*.jsonl"))
        self.assertEqual(len(raw_files), 1)
        self.assertIn("keep", raw_files[0].read_text())


if __name__ == "__main__":
    unittest.main()
