"""Tests of dream-extract-precheck.py — the feeder of the fact store."""

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_dream import DreamFixture, dream, FAMILY_CHAT, EXTERNAL_CHAT  # noqa: E402

ROOT = Path(__file__).parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


extract = _load("dream_extract", ROOT / "scripts/dream-extract-precheck.py")


class ExtractPrecheckTests(DreamFixture):
    def setUp(self):
        super().setUp()
        self._old_home = extract.HOME
        extract.HOME = self.home
        self.cfg = dict(dream.CONFIG, extract={"min_messages": 3, "max_messages": 4, "backfill_days": 30})

    def tearDown(self):
        extract.HOME = self._old_home
        super().tearDown()

    def _now(self):
        return datetime.now(timezone.utc).timestamp()

    def test_below_gate_returns_none(self):
        self.add_message("Люблю утренний кофе", days_ago=1)
        payload, cursor, total = extract.build_payload(dream, self.cfg, {}, self._now())
        self.assertIsNone(payload)
        self.assertEqual(total, 1)

    def test_chunk_and_cursor(self):
        for i in range(6):
            self.add_message(f"Сообщение номер {i} про утренний кофе", days_ago=6 - i)
        payload, cursor, total = extract.build_payload(dream, self.cfg, {}, self._now())
        self.assertEqual(total, 6)
        self.assertEqual(len(payload["messages"]), 4)          # max_messages
        self.assertEqual(payload["window"]["remaining_after_this_chunk"], 2)
        # Next run continues after the cursor.
        payload2, cursor2, total2 = extract.build_payload(dream, self.cfg, {"since_ts": cursor}, self._now())
        self.assertEqual(total2, 2)
        self.assertIsNone(payload2)  # 2 < min_messages

    def test_untrusted_and_cron_messages_excluded(self):
        for i in range(4):
            self.add_message(f"Чужой чат {i} про кофе", days_ago=1, chat_id=EXTERNAL_CHAT)
            self.add_message(f"Cron prompt {i} about coffee", days_ago=1, source="cron",
                             chat_type=None, chat_id=None)
        payload, _, total = extract.build_payload(dream, self.cfg, {}, self._now())
        self.assertIsNone(payload)
        self.assertEqual(total, 0)

    def test_existing_facts_included_and_secrets_dropped(self):
        for i in range(4):
            self.add_message(f"Сообщение {i} про утренний кофе", days_ago=1)
        self.add_fact("Марина пьёт кофе только до полудня")
        self.add_fact("API-ключ проекта: sk-abc123def456ghi789jklmno")
        payload, _, _ = extract.build_payload(dream, self.cfg, {}, self._now())
        self.assertIn("Марина пьёт кофе только до полудня", payload["existing_facts"])
        self.assertFalse(any("sk-abc" in f for f in payload["existing_facts"]))
        self.assertIn("not instructions", payload["note"])

    def test_state_roundtrip_private(self):
        path = self.home / "cache" / "dream-extract-state.json"
        extract.save_state(path, {"since_ts": 123.0})
        self.assertEqual(extract.load_state(path)["since_ts"], 123.0)
        self.assertEqual(extract.load_state(self.home / "missing.json"), {})


if __name__ == "__main__":
    unittest.main()
