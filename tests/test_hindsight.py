from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from telegram_hindsight.hindsight import HindsightClient, HindsightError
from telegram_hindsight.pipeline import build_document


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(body) if body else None,
            }
        )
        return self.responses.pop(0)


def result():
    return build_document(
        [
            {
                "id": 1,
                "type": "message",
                "date": "2026-11-25T09:00:00Z",
                "from": "Alice",
                "from_id": "user-1",
                "text": "Send the requirements on Friday.",
                "chat": {"id": -1003701057366},
            }
        ],
        "-1003701057366",
        now=datetime(2026, 11, 26, tzinfo=timezone.utc),
    )


class HindsightTests(unittest.TestCase):
    def test_replace_uses_verified_retain_contract(self):
        transport = RecordingTransport([(200, {"success": True, "bank_id": "staging", "items_count": 1, "async": False})])
        client = HindsightClient("https://example.test", "secret", transport=transport)
        client.replace_document("staging", result())
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://example.test/v1/default/banks/staging/memories")
        item = call["body"]["items"][0]
        self.assertEqual(item["update_mode"], "replace")
        self.assertEqual(item["document_id"], "telegram:group:-1003701057366:memory")
        self.assertEqual(call["headers"]["Authorization"], "Bearer secret")

    def test_consolidate_uses_stable_scope(self):
        transport = RecordingTransport([(200, {"operation_id": "op-1", "deduplicated": False})])
        client = HindsightClient("https://example.test", "secret", transport=transport)
        response = client.consolidate("staging", [["source:telegram", "chat:-1003701057366"]])
        self.assertEqual(response["operation_id"], "op-1")
        self.assertIn("/consolidate", transport.calls[0]["url"])

    def test_missing_document_returns_none(self):
        transport = RecordingTransport([(404, {"detail": "not found"})])
        client = HindsightClient("https://example.test", "secret", transport=transport)
        self.assertIsNone(client.get_document("staging", "missing"))

    def test_failed_retain_raises(self):
        transport = RecordingTransport([(401, {"detail": "bad key"})])
        client = HindsightClient("https://example.test", "secret", transport=transport)
        with self.assertRaises(HindsightError) as caught:
            client.replace_document("staging", result())
        self.assertEqual(caught.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
