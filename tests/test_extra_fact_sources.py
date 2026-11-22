"""Extra fact sources: the `extra_fact_sources` config key (fork feature, 3.9.0).

Each entry adds one more store of candidate facts next to the main one (`fact_source`, holographic by default).
Its facts carry the id `<label>#<id>` and the marker `source: <label>`, never `store`. They are corroborated by the
ordinary trusted messages, and they never reach the write paths of the holographic store (fact_feedback, keywords,
merge), nor the conflict and decay checks, nor the cloud judge.

Written first (red): the feature does not exist yet, so every test below fails on the missing interface or on the
missing behaviour, never on a bug in the test itself.
"""

import contextlib
import io
import json
import sqlite3
import unittest

import home_guard  # noqa: F401  (before any script is loaded: a throwaway HERMES_HOME)
from test_dream import FAMILY_CHAT, dream, precheck, reject, _ts  # noqa: E402
from test_fact_sources import FactSourceFixture  # noqa: E402

FACT_TEXT = "Марина предпочитает утренние тренировки по вторникам"
KANBAN_CARD = "Кактус на окне любит редкий полив"


class ExtraFixture(FactSourceFixture):
    """A temporary home with the main store (holographic, filled by `add_fact`) and helpers for the extra stores."""

    def _jsonl(self, name, rows):
        path = self.home / name
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return path

    def _sqlite(self, name, table, columns, rows):
        path = self.home / name
        con = sqlite3.connect(path)
        con.execute(f"create table {table} ({', '.join(columns)})")
        con.executemany(f"insert into {table} values ({', '.join('?' * len(columns))})", rows)
        con.commit()
        con.close()
        return path

    def _kanban_jsonl(self, rows=None):
        """A jsonl extra source labelled `kanban`, in the home, with its own column mapping."""
        rows = rows if rows is not None else [
            {"key": "x", "text": "Фикус поливают по субботам", "stamp": _ts(1).isoformat()},
            {"key": "y", "text": "Гортензия любит тень", "stamp": _ts(1).isoformat()},
        ]
        self._jsonl("cards.jsonl", rows)
        return {"label": "kanban", "fact_source": "jsonl", "fact_store_path": "cards.jsonl",
                "fact_columns": {"id": "key", "content": "text", "created_at": "stamp"}}

    def _corroborated_messages(self, days=(1, 3, 5)):
        for d in days:
            self.add_message(f"Снова утренние тренировки по вторникам, день {d}", days_ago=d)

    def _settings_and_warnings(self, entries):
        self.use(extra_fact_sources=entries)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            labels = [e["label"] for e in dream.extra_fact_sources_settings()]
        return labels, err.getvalue()


class WithoutTheKeyTests(ExtraFixture):
    def test_without_the_key_there_are_no_extra_sources_and_the_facts_are_the_same_as_before(self):
        self.add_fact(FACT_TEXT, trust=0.9, rc=2, helpful=2)
        self.use()  # no extra_fact_sources at all
        self.assertEqual(dream.extra_fact_sources_settings(), [])
        self.assertEqual([f["fact_id"] for f in dream.load_all_facts()],
                         [f["fact_id"] for f in dream.load_facts()])
        self.assertEqual(len(dream.load_all_facts()), 1)


class ExtraSourceLoadingTests(ExtraFixture):
    def test_jsonl_extra_source_adds_prefixed_facts_marked_with_source_and_never_store(self):
        self.use(extra_fact_sources=[self._kanban_jsonl()])
        facts = dream.load_extra_facts()
        self.assertEqual(sorted(f["fact_id"] for f in facts), ["kanban#x", "kanban#y"])
        self.assertEqual({f["source"] for f in facts}, {"kanban"})
        self.assertTrue(all("store" not in f for f in facts))
        self.assertIn("Фикус поливают по субботам", [f["content"] for f in facts])

    def test_extra_entry_uses_its_own_columns_not_the_root_fact_columns(self):
        # The root `fact_columns` maps `content` to a key this file does not have: the entry's own mapping must win.
        entry = self._kanban_jsonl()
        self.use(fact_columns={"content": "memory"}, extra_fact_sources=[entry])
        self.assertEqual(sorted(f["content"] for f in dream.load_extra_facts()),
                         ["Гортензия любит тень", "Фикус поливают по субботам"])
        self.assertIn("kanban#x", [f["fact_id"] for f in dream.load_all_facts()])

    def test_sqlite_extra_source_reads_its_own_table_and_columns(self):
        self._sqlite("kanban.db", "notes", ["uuid text", "body text", "stamp text"],
                     [("a1", KANBAN_CARD, _ts(1).strftime("%Y-%m-%d %H:%M:%S"))])
        self.use(extra_fact_sources=[{
            "label": "kanban", "fact_source": "sqlite", "fact_store_path": "kanban.db",
            "fact_table": "notes", "fact_columns": {"id": "uuid", "content": "body", "created_at": "stamp"}}])
        facts = dream.load_extra_facts()
        self.assertEqual([(f["fact_id"], f["content"], f["source"]) for f in facts],
                         [("kanban#a1", KANBAN_CARD, "kanban")])


class InvalidEntryTests(ExtraFixture):
    VALID = {"label": "kanban", "fact_source": "jsonl", "fact_store_path": "cards.jsonl"}

    def test_entry_without_label_is_warned_and_skipped(self):
        labels, err = self._settings_and_warnings([
            {"fact_source": "jsonl", "fact_store_path": "cards.jsonl"}, self.VALID])
        self.assertEqual(labels, ["kanban"])
        self.assertIn("[dream] warn:", err)

    def test_duplicate_label_is_warned_and_not_kept_twice(self):
        labels, err = self._settings_and_warnings([self.VALID, dict(self.VALID, fact_store_path="other.jsonl")])
        self.assertLessEqual(labels.count("kanban"), 1)
        self.assertIn("[dream] warn:", err)

    def test_label_outside_the_allowed_pattern_is_warned_and_skipped(self):
        labels, err = self._settings_and_warnings([dict(self.VALID, label="kan ban"), self.VALID])
        self.assertEqual(labels, ["kanban"])
        self.assertIn("[dream] warn:", err)

    def test_unknown_fact_source_is_warned_and_skipped(self):
        labels, err = self._settings_and_warnings([
            {"label": "mem0", "fact_source": "mem0", "fact_store_path": "m.db"}, self.VALID])
        self.assertEqual(labels, ["kanban"])
        self.assertIn("[dream] warn:", err)

    def test_entry_without_path_is_warned_and_skipped(self):
        labels, err = self._settings_and_warnings([
            {"label": "notes", "fact_source": "jsonl"}, self.VALID])
        self.assertEqual(labels, ["kanban"])
        self.assertIn("[dream] warn:", err)


class UnreadableSourceTests(ExtraFixture):
    def test_missing_jsonl_file_is_skipped_with_a_warning_and_the_night_goes_on(self):
        self.add_fact(FACT_TEXT, trust=0.9, rc=2, helpful=2)
        for d in (1, 3, 5):
            self.add_message(f"Снова утренние тренировки по вторникам, день {d}", days_ago=d)
        self.use(extra_fact_sources=[{"label": "kanban", "fact_source": "jsonl",
                                      "fact_store_path": "absent.jsonl"}])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(dream.load_extra_facts(), [])
        self.assertIn("[dream] warn:", err.getvalue())
        out = self.run_dream()
        self.assertEqual(len(out["promotions"]), 1)  # the main source is still reviewed

    def test_sqlite_extra_source_without_its_content_column_is_skipped_with_a_warning(self):
        self._sqlite("kanban.db", "notes", ["uuid text", "stamp text"], [("a1", "2026-11-20 08:00:00")])
        self.use(extra_fact_sources=[{
            "label": "kanban", "fact_source": "sqlite", "fact_store_path": "kanban.db",
            "fact_table": "notes", "fact_columns": {"id": "uuid", "content": "body"}}])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(dream.load_extra_facts(), [])
        self.assertIn("[dream] warn:", err.getvalue())


class CorroborationTests(ExtraFixture):
    def test_extra_fact_is_corroborated_by_ordinary_trusted_messages(self):
        self.use(extra_fact_sources=[{
            "label": "kanban", "fact_source": "jsonl", "fact_store_path": "cards.jsonl",
            "fact_columns": {"id": "id", "content": "content"}}])
        self._jsonl("cards.jsonl", [{"id": "k1", "content": FACT_TEXT, "created_at": _ts(1).isoformat()}])
        self._corroborated_messages()
        out = self.run_dream()
        self.assertIn("kanban#k1", [p["fact_id"] for p in out["promotions"]])


class NoWriteBackTests(ExtraFixture):
    def test_extra_fact_gets_no_feedback_keywords_or_merge_item_even_beside_a_main_twin(self):
        # Same text in the main store and in the extra source: the two are not merged, and the extra one
        # gets no call that would write the holographic store.
        self.add_fact(FACT_TEXT, trust=0.9, rc=2, helpful=2)
        self._corroborated_messages()
        self.use(extra_fact_sources=[{
            "label": "kanban", "fact_source": "jsonl", "fact_store_path": "cards.jsonl",
            "fact_columns": {"id": "id", "content": "content"}}])
        self._jsonl("cards.jsonl", [{"id": "k1", "content": FACT_TEXT, "created_at": _ts(1).isoformat()}])
        self.assertIn("kanban#k1", [f["fact_id"] for f in dream.load_all_facts()])
        out = self.run_dream()
        self.assertIn("kanban#k1", [p["fact_id"] for p in out["promotions"]])  # it is reviewed, as any fact
        for bucket in ("trust_feedback", "keywords_needed", "duplicate_pairs", "confirm", "confirm_ask"):
            self.assertNotIn("kanban#k1", json.dumps(out.get(bucket, []), ensure_ascii=False), bucket)

    def test_extra_fact_never_conflicts_or_decays(self):
        self.mem_md.write_text("§\nКактус на окне поливают 2 раза в неделю\n", encoding="utf-8")
        self.use(extra_fact_sources=[{
            "label": "kanban", "fact_source": "jsonl", "fact_store_path": "cards.jsonl",
            "fact_columns": {"id": "id", "content": "content", "created_at": "created_at"}}])
        self._jsonl("cards.jsonl", [
            {"id": "k2", "content": "Кактус на окне поливают 3 раза в неделю", "created_at": _ts(1).isoformat()},
            {"id": "k3", "content": "Старый кактус в углу никто не трогает", "created_at": _ts(400).isoformat()},
        ])
        self.assertTrue({"kanban#k2", "kanban#k3"} <= {f["fact_id"] for f in dream.load_all_facts()})
        out = self.run_dream()
        for bucket in ("conflicts", "fact_decays"):
            text = json.dumps(out[bucket], ensure_ascii=False)
            self.assertNotIn("kanban#k2", text, bucket)
            self.assertNotIn("kanban#k3", text, bucket)


class JudgeTests(ExtraFixture):
    def test_extra_fact_is_never_sent_to_the_judge(self):
        js = dream.judge_settings({"mode": "shadow", "providers": ["jev"], "send_from": "trusted",
                                   "send_sources": ["telegram"]})
        m = {"session_found": True, "session_id": "s-1", "source": "telegram", "chat_id": FAMILY_CHAT,
             "chat_type": "group", "locally_trusted": True, "own_only": False}
        main_fact = {"fact_id": 1, "content": FACT_TEXT}
        extra_fact = {"fact_id": "kanban#k1", "content": FACT_TEXT, "source": "kanban"}
        self.assertIsNone(dream.judge_permission(main_fact, m, js))  # control: the same message may leave
        self.assertIsNotNone(dream.judge_permission(extra_fact, m, js))


class PublishTests(ExtraFixture):
    def test_published_promotion_of_an_extra_fact_keeps_source_and_not_store(self):
        self.use(extra_fact_sources=[{
            "label": "kanban", "fact_source": "jsonl", "fact_store_path": "cards.jsonl",
            "fact_columns": {"id": "id", "content": "content", "created_at": "created_at"}}])
        self._jsonl("cards.jsonl", [{"id": "k1", "content": FACT_TEXT, "created_at": _ts(1).isoformat()}])
        self._corroborated_messages()
        out = self.run_dream()
        item = next((p for p in out["promotions"] if p["fact_id"] == "kanban#k1"), None)
        self.assertIsNotNone(item, "the extra fact is not among the promotions")
        self.assertEqual(item.get("source"), "kanban")
        self.assertNotIn("store", item)

    def test_precheck_lets_the_source_field_through(self):
        self.assertIn("source", precheck.PUBLISHED_FIELDS)


class RejectTests(ExtraFixture):
    def test_reject_finds_an_extra_fact_in_its_own_source(self):
        cards = self._jsonl("cards.jsonl", [{"id": "x", "content": "Фикус поливают по субботам"}])
        self.use(extra_fact_sources=[{"label": "kanban", "fact_source": "jsonl",
                                      "fact_store_path": str(cards)}])
        reject._DREAM = dream
        try:
            self.assertEqual(reject.fetch_content("kanban#x"), "Фикус поливают по субботам")
        finally:
            reject._DREAM = None


class ReportTests(ExtraFixture):
    def _run_with_two_extra(self):
        self.add_fact(FACT_TEXT, trust=0.9, rc=2, helpful=2)
        self.use(extra_fact_sources=[self._kanban_jsonl()])
        return self.run_dream()

    def test_dry_run_summary_has_the_extra_sources_line(self):
        out = self._run_with_two_extra()
        self.assertEqual(len(dream.load_extra_facts()), 2)
        text = dream.dry_run_summary(out, "holographic")
        self.assertIn("extra sources: kanban 2 facts", text)

    def test_json_report_has_an_extra_sources_block(self):
        out = self._run_with_two_extra()
        self.assertIn("extra_sources", out)
        block = json.dumps(out["extra_sources"], ensure_ascii=False)
        self.assertIn("kanban", block)
        self.assertIn("2", block)

    def test_stats_facts_counts_the_main_source_only(self):
        self.add_fact(FACT_TEXT, trust=0.9, rc=2, helpful=2)
        self.use(extra_fact_sources=[self._kanban_jsonl()])
        self.assertEqual(len(dream.load_extra_facts()), 2)
        out = self.run_dream()
        self.assertEqual(out["stats"]["facts"], 1)


class InstallCheckTests(ExtraFixture):
    def test_install_check_reports_each_extra_source_ok_when_present_and_warn_when_absent(self):
        from test_install import install  # noqa: E402

        class Recorder(install.Report):
            def __init__(self):
                super().__init__()
                self.seen = []

            def ok(self, msg):
                self.seen.append(("ok", msg))

            def warn(self, msg):
                self.seen.append(("warn", msg))

            def bad(self, msg):
                self.seen.append(("bad", msg))

        self._jsonl("facts.jsonl", [{"id": "f-1", "content": "Фикус поливают по субботам"}])
        self._jsonl("cards.jsonl", [{"id": "x", "content": KANBAN_CARD}])
        self.use(fact_source="jsonl", fact_store_path="facts.jsonl", extra_fact_sources=[
            {"label": "kanban", "fact_source": "jsonl", "fact_store_path": "cards.jsonl"},
            {"label": "other", "fact_source": "jsonl", "fact_store_path": "missing.jsonl"}])
        report = Recorder()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            install.check_fact_source(self.home, dream, report)
        ok_text = " ".join(m for kind, m in report.seen if kind == "ok")
        warn_text = " ".join(m for kind, m in report.seen if kind == "warn")
        self.assertIn("kanban", ok_text)
        self.assertIn("other", warn_text)
        self.assertNotIn("kanban", warn_text)


if __name__ == "__main__":
    unittest.main()
