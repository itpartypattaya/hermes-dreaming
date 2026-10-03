"""Fact sources: where the dream takes candidate facts from (`fact_source`).

holographic — the Hermes holographic provider (covered by every other test);
none        — no fact store, the memory files alone;
sqlite      — any SQLite table, columns mapped by `fact_columns`;
jsonl       — one JSON object per line, keys mapped the same way.
"""

import contextlib
import io
import json
import os
import sqlite3
import sys
import unittest

from test_dream import DreamFixture, dream, reject, _ts  # noqa: E402
from test_extract_precheck import extract  # noqa: E402


class FactSourceFixture(DreamFixture):
    def setUp(self):
        super().setUp()
        self._old_config = dream.CONFIG
        self._old_env = os.environ.pop("DREAM_FACT_SOURCE", None)

    def tearDown(self):
        dream.CONFIG = self._old_config
        if self._old_env is not None:
            os.environ["DREAM_FACT_SOURCE"] = self._old_env
        super().tearDown()

    def use(self, **cfg):
        dream.CONFIG = dict(self._old_config, **cfg)


class SourceSelectionTests(FactSourceFixture):
    def test_default_is_holographic(self):
        self.use()
        self.assertEqual(dream.fact_source(), "holographic")

    def test_env_beats_config(self):
        self.use(fact_source="jsonl")
        os.environ["DREAM_FACT_SOURCE"] = "none"
        try:
            self.assertEqual(dream.fact_source(), "none")
        finally:
            os.environ.pop("DREAM_FACT_SOURCE", None)

    def test_unknown_source_is_an_error_not_silence(self):
        self.use(fact_source="mem0")
        with self.assertRaises(ValueError):
            dream.fact_source()

    def test_generic_source_without_path_has_no_facts(self):
        self.use(fact_source="sqlite")
        self.assertIsNone(dream.fact_store_path())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(dream.load_facts(), [])
        self.assertIn("fact_store_path is not set", err.getvalue())


class NoneSourceTests(FactSourceFixture):
    def test_memory_files_alone(self):
        self.add_fact("Марина любит жасминовый чай")          # present, but not read
        self.mem_md.write_text("§\nСтаринный граммофон хранится в кладовке на верхней полке.\n",
                               encoding="utf-8")
        self.use(fact_source="none")
        out = self.run_dream()
        self.assertEqual(out["stats"]["facts"], 0)
        self.assertEqual(out["promotions"], [])
        self.assertEqual(len(out["md_decays"]), 1)               # memory checks still work
        self.assertIn("memory_usage", out)

    def test_extraction_gate_stays_closed(self):
        for i in range(20):
            self.add_message(f"Сообщение {i} про утренний кофе", days_ago=1)
        self.use(fact_source="none", extract={"min_messages": 3})
        saved = (extract._dream, extract.HOME)
        extract._dream, extract.HOME = (lambda: dream), self.home
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                extract.main()
        finally:
            extract._dream, extract.HOME = saved
        self.assertEqual(json.loads(out.getvalue()), {"wakeAgent": False})
        self.assertIn("fact_source=none", err.getvalue())


class GenericSqliteTests(FactSourceFixture):
    def _store(self, rows):
        path = self.home / "export.db"
        con = sqlite3.connect(path)
        con.execute("create table memories (uuid text, text text, created integer,"
                    " labels text, extra text)")
        con.executemany("insert into memories values (?,?,?,?,?)", rows)
        con.commit()
        con.close()
        return path

    def test_columns_are_mapped_and_defaults_filled(self):
        created_ms = int(_ts(2).timestamp() * 1000)
        path = self._store([("a1", "Марина предпочитает утренние тренировки", created_ms, "sport", "x"),
                            ("a2", "  ", created_ms, "", "")])                # empty → skipped
        self.use(fact_source="sqlite", fact_store_path=str(path), fact_table="memories",
                 fact_columns={"id": "uuid", "content": "text", "created_at": "created",
                               "tags": "labels"})
        facts = dream.load_facts()
        self.assertEqual(len(facts), 1)
        f = facts[0]
        self.assertEqual((f["fact_id"], f["tags"], f["trust_score"], f["retrieval_count"]),
                         ("a1", "sport", 0.5, 0))
        self.assertEqual(f["updated_at"], f["created_at"])             # falls back to created
        self.assertAlmostEqual(dream._parse_ts(f["created_at"]).timestamp(), created_ms / 1000, delta=1)

    def test_scored_like_any_fact(self):
        path = self._store([("a1", "Марина предпочитает утренние тренировки по вторникам",
                             int(_ts(1).timestamp()), "", "")])
        for d in (1, 3, 5):
            self.add_message(f"Снова утренние тренировки по вторникам, день {d}", days_ago=d)
        self.use(fact_source="sqlite", fact_store_path=str(path), fact_table="memories",
                 fact_columns={"id": "uuid", "content": "text", "created_at": "created"})
        out = self.run_dream()
        self.assertEqual(out["stats"]["facts"], 1)
        self.assertEqual([p["fact_id"] for p in out["promotions"]], ["a1"])

    def test_missing_required_column_is_an_error(self):
        path = self._store([])
        self.use(fact_source="sqlite", fact_store_path=str(path), fact_table="memories",
                 fact_columns={"id": "uuid", "content": "body"})
        with self.assertRaises(ValueError):
            dream.load_facts()

    def test_table_name_is_not_sql(self):
        path = self._store([])
        self.use(fact_source="sqlite", fact_store_path=str(path), fact_table="memories; drop table x")
        with self.assertRaises(ValueError):
            dream.load_facts()

    def test_store_is_opened_read_only(self):
        path = self._store([("a1", "Марина любит чай", 0, "", "")])
        before = path.stat().st_mtime_ns
        self.use(fact_source="sqlite", fact_store_path=str(path), fact_table="memories",
                 fact_columns={"id": "uuid", "content": "text"})
        dream.load_facts()
        self.assertEqual(path.stat().st_mtime_ns, before)


class JsonlTests(FactSourceFixture):
    def _write(self, lines):
        path = self.home / "facts.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_lines_mapped_broken_line_skipped(self):
        path = self._write([
            json.dumps({"id": "m-1", "memory": "Марина любит жасминовый чай",
                        "updated_at": "2026-09-30T10:00:00Z", "tags": ["tea", "family"],
                        "trust": 0.8}, ensure_ascii=False),
            "{not json",
            json.dumps({"id": "m-2"}),                                   # no content → skipped
        ])
        self.use(fact_source="jsonl", fact_store_path=str(path),
                 fact_columns={"content": "memory"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            facts = dream.load_facts()
        self.assertEqual(len(facts), 1)
        f = facts[0]
        self.assertEqual((f["fact_id"], f["tags"], f["trust_score"], f["updated_at"]),
                         ("m-1", "tea,family", 0.8, "2026-09-30 10:00:00"))
        self.assertIn(":2 is not JSON", err.getvalue())

    def test_reject_and_explain_take_string_ids(self):
        path = self._write([json.dumps({"id": "m-7", "content": "Марина любит жасминовый чай"},
                                       ensure_ascii=False)])
        self.use(fact_source="jsonl", fact_store_path=str(path))
        reject._DREAM = dream
        try:
            self.assertEqual(reject.fetch_content("m-7"), "Марина любит жасминовый чай")
            self.assertIsNone(reject.fetch_content("m-8"))
        finally:
            reject._DREAM = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dream.explain("m-7", str(self.mem_md), 60), 0)
        self.assertIn("fact m-7", out.getvalue())


class DryRunTests(DreamFixture):
    """`dream.py --dry-run`: the safe "what would tonight's dream propose" — for
    a human, and for an agent asked to look without touching anything."""

    def _main(self, *argv):
        from test_dream import TEST_CONFIG
        cfg = self.home / "dreaming.json"
        cfg.write_text(json.dumps(dict(TEST_CONFIG, trusted_chat_ids=["-100111"])), encoding="utf-8")
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = dream.main(["--config", str(cfg), *argv])
        finally:
            dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, TEST_CONFIG))
            dream.HOME = str(self.home)
            dream.TRUSTED_CHAT_IDS = {"-100111"}
        return rc, out.getvalue()

    def test_reports_and_writes_nothing(self):
        self.add_fact("Марина предпочитает утренние тренировки по вторникам",
                      trust=0.9, rc=2, helpful=2, tags="family,routine")
        for d in (1, 3, 5):
            self.add_message(f"Снова ходила на утренние тренировки, день {d}", days_ago=d)
        self.mem_md.write_text("§\nСтаринный граммофон хранится в кладовке на верхней полке.\n",
                               encoding="utf-8")
        cache = self.home / "cache"
        cache.mkdir()
        seen = cache / "dream-seen.json"
        seen.write_text("{}", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in cache.iterdir()}
        mem_before = self.mem_md.read_bytes()

        rc, out = self._main("--dry-run")

        self.assertEqual(rc, 0)
        self.assertIn("Dry run", out)
        self.assertIn("promotions (candidates for durable memory): 1", out)
        self.assertIn("утренние тренировки", out)
        self.assertIn('"still relevant?": 1', out)
        self.assertIn("would wake the agent", out)
        self.assertEqual({p.name: p.read_bytes() for p in cache.iterdir()}, before)
        self.assertEqual(self.mem_md.read_bytes(), mem_before)
        self.assertFalse((self.home / "memories" / "DREAMS.md").exists())
        self.assertFalse(list(self.home.glob("**/dream-snapshot.json")))

    def test_hermes_home_flag_points_the_pass_at_another_home(self):
        """The cron gates pass --hermes-home: everything — config, fact store,
        transcripts, memory files — must come from that home."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            other = type(self.home)(tmp)
            (other / "dreaming.json").write_text(
                json.dumps({"fact_source": "jsonl", "fact_store_path": "facts.jsonl"}), encoding="utf-8")
            (other / "facts.jsonl").write_text(
                json.dumps({"id": "f-1", "content": "Фикус поливают по субботам"}) + "\n",
                encoding="utf-8")
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = dream.main(["--hermes-home", str(other), "--dry-run"])
            finally:
                from test_dream import TEST_CONFIG
                dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, TEST_CONFIG))
                dream.HOME = str(self.home)
                dream.TRUSTED_CHAT_IDS = {"-100111"}
        self.assertEqual(rc, 0)
        self.assertIn("fact source: jsonl", out.getvalue())
        self.assertIn("facts: 1", out.getvalue())

    def test_quiet_night(self):
        rc, out = self._main("--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("nothing to do", out)


if __name__ == "__main__":
    unittest.main()
