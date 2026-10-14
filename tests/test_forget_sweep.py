"""Tests of the forget sweep (holographic >= 0.8.0): `dream.py --forget-sweep`, the gate
dream-forget-sweep.py, [#id] tags in the diary and the `fact_history` flag.

The store follows the 0.8.0 schema as far as the dream reads it: `facts`, `fact_versions` (only
that it exists) and `forgotten` (tombstones: id and time, no text). Fixtures are Russian on purpose.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_HERE = Path(__file__).resolve().parents[1]
ROOT = _HERE / "skills" / "dreaming" if (_HERE / "skills" / "dreaming" / "SKILL.md").is_file() else _HERE
dream = _load("dream", ROOT / "scripts/dream.py")

KEPT = "Ольга ходит на плавание по вторникам и четвергам после работы"
FORGOTTEN_OLD = ("Виктор хранит запасной ключ от велосипеда у соседа Игоря на третьем этаже "
                 "дома у старого парка")
FORGOTTEN_NEW = "Ольга не любит острую еду и всегда просит без перца в кафе"


class ForgetSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for d in ("memories", "cache", "cron/output/job-dream", "cron/output/job-other"):
            (self.home / d).mkdir(parents=True)
        self.db = self.home / "memory_store.db"
        con = sqlite3.connect(self.db)
        con.executescript(
            "create table facts (fact_id integer primary key autoincrement, content text unique,"
            " category text default 'general', tags text default '', trust_score real default 0.5,"
            " retrieval_count integer default 0, helpful_count integer default 0,"
            " created_at timestamp default current_timestamp, updated_at timestamp default current_timestamp);"
            "create table fact_versions (version_id integer primary key, fact_id integer, seq integer,"
            " content text, status text);"
            "create table forgotten (fact_id integer primary key, forgotten_at text, digests text);"
            "create table meta (key text primary key, value text);"
            "insert into meta values ('history_schema', '1');")
        for text in (KEPT, FORGOTTEN_OLD, FORGOTTEN_NEW):
            con.execute("insert into facts (content) values (?)", (text,))
        con.commit()
        con.close()
        self._old = (dream.HOME, dream.CONFIG)
        dream.HOME = str(self.home)
        (self.home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
            {"id": "job-dream", "script": "dream-precheck.py"},
            {"id": "job-other", "script": "kid-reminder.py"}]}), encoding="utf-8")

    def tearDown(self):
        dream.HOME, dream.CONFIG = self._old
        self.tmp.cleanup()

    def path(self, rel):
        return self.home / rel

    def forget(self, *ids):
        con = sqlite3.connect(self.db)
        for i in ids:
            con.execute("delete from facts where fact_id = ?", (i,))
            con.execute("insert into forgotten values (?, '2026-10-12 12:00:00', '[]')", (i,))
        con.commit()
        con.close()

    def seed_copies(self):
        """What the dream copied while facts 2 and 3 still existed: the diary (old untagged lines and new
        tagged ones), its archive, the payload, the reject list, the cooldowns, the cron logs."""
        fp = dream._fact_fingerprint
        diary = "\n".join([
            "## Сон 2026-09-01",
            f"- (0.71, mentions 4/3d) {FORGOTTEN_OLD[:85]}",  # before 2.4.0: no tag
            f"- {KEPT[:85]}",
            f"**Facts never used (2):** {FORGOTTEN_OLD[:45]}; {KEPT[:45]}",
            "",
            "## Сон 2026-10-01",
            f"- {FORGOTTEN_NEW[:85]} [#3]",
            f"**Possible updates of existing entries (2):** {KEPT[:45]} [#1]; {FORGOTTEN_NEW[:45]} [#3]",
            f"**Facts never used (1):** {FORGOTTEN_NEW[:45]} [#3]",
            "",
        ])
        self.path("memories/DREAMS.md").write_text(diary, encoding="utf-8")
        self.path("memories/DREAMS.md.archive.md").write_text(
            f"## Сон 2026-07-01\n- {FORGOTTEN_OLD[:85]}\n- {KEPT[:85]}\n", encoding="utf-8")
        payload = {"promotions": [{"fact_id": 2, "content": FORGOTTEN_OLD,
                                   "duplicates": [{"fact_id": 3, "content": FORGOTTEN_NEW}]},
                                  {"fact_id": 1, "content": KEPT}],
                   "fact_decays": [{"fact_id": "3", "content": FORGOTTEN_NEW}], "stats": {"facts": 3}}
        self.path("cache/dream.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        rejected = {fp(FORGOTTEN_OLD): {"fact_id": "2", "content": FORGOTTEN_OLD, "fingerprint": fp(FORGOTTEN_OLD)},
                    fp(FORGOTTEN_NEW): {"fact_id": None, "content": None, "fingerprint": fp(FORGOTTEN_NEW)},
                    fp(KEPT): {"fact_id": "1", "content": KEPT, "fingerprint": fp(KEPT)}}
        self.path("cache/dream-rejected.json").write_text(json.dumps(rejected, ensure_ascii=False), encoding="utf-8")
        seen = {"new_facts": {fp(FORGOTTEN_OLD): {"at": "2026-10-01T00:00:00+00:00", "fact_id": 2},
                              fp(KEPT): {"at": "2026-10-01T00:00:00+00:00", "fact_id": 1}},
                "conflicts": {fp(FORGOTTEN_NEW): {"at": "2026-10-01T00:00:00+00:00", "fact_id": None}},
                "_pending": {"generated_at": "x", "seen": {"new_facts": [fp(FORGOTTEN_OLD), fp(KEPT)]}}}
        self.path("cache/dream-seen.json").write_text(json.dumps(seen), encoding="utf-8")
        self.path("cron/output/job-dream/2026-10-01_03-00.md").write_text(
            'prompt {"promotions":[{"fact_id":2,"content":"…"}]}', encoding="utf-8")
        self.path("cron/output/job-dream/2026-10-02_03-00.md").write_text(
            'prompt {"promotions":[{"fact_id":1,"content":"…"}]}', encoding="utf-8")
        self.path("cron/output/job-other/2026-10-01.md").write_text('{"fact_id": 2}', encoding="utf-8")

    def test_sweep_removes_every_copy_and_keeps_the_rest(self):
        dream.forget_sweep(db=str(self.db))  # while the facts exist: the diary index
        index = json.loads(self.path("cache/dream-diary-index.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(index), ["1", "2", "3"])
        self.seed_copies()
        self.forget(2, 3)
        summary = dream.forget_sweep(db=str(self.db))
        self.assertEqual(summary["swept"], 2)

        diary = self.path("memories/DREAMS.md").read_text(encoding="utf-8")
        archive = self.path("memories/DREAMS.md.archive.md").read_text(encoding="utf-8")
        for text in (diary, archive):
            self.assertNotIn(FORGOTTEN_OLD[:30], text)
            self.assertNotIn(FORGOTTEN_NEW[:30], text)
            self.assertIn(KEPT[:30], text)
        self.assertIn(f"**Possible updates of existing entries (2):** {KEPT[:45]} [#1]", diary)
        self.assertIn(f"**Facts never used (2):** {KEPT[:45]}", diary)
        self.assertNotIn("**Facts never used (1):**", diary)  # the list is empty: no line

        payload = json.loads(self.path("cache/dream.json").read_text(encoding="utf-8"))
        self.assertEqual([p["fact_id"] for p in payload["promotions"]], [1])
        self.assertEqual(payload["fact_decays"], [])
        rejected = json.loads(self.path("cache/dream-rejected.json").read_text(encoding="utf-8"))
        self.assertEqual(list(rejected), [dream._fact_fingerprint(KEPT)])
        seen = json.loads(self.path("cache/dream-seen.json").read_text(encoding="utf-8"))
        self.assertEqual(list(seen["new_facts"]), [dream._fact_fingerprint(KEPT)])
        self.assertEqual(seen["conflicts"], {})
        self.assertEqual(seen["_pending"]["seen"]["new_facts"], [dream._fact_fingerprint(KEPT)])

        self.assertEqual(sorted(os.listdir(self.path("cron/output/job-dream"))), ["2026-10-02_03-00.md"])
        self.assertEqual(os.listdir(self.path("cron/output/job-other")), ["2026-10-01.md"])  # other jobs' logs are left alone
        index = json.loads(self.path("cache/dream-diary-index.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(index), ["1"])
        # the hashes moved to the sweep state: the owner of a diary backup cleans it with them
        moved = dream.forgotten_diary_index([2, 3])
        self.assertEqual(moved["2"]["previews"], dream._preview_hashes(FORGOTTEN_OLD))
        backup = "\n".join([f"- {FORGOTTEN_OLD[:85]}", f"- {KEPT[:85]}"])
        self.assertEqual(dream.scrub_diary_text(backup, ["2"], moved)[0], f"- {KEPT[:85]}")

        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and p.suffix in (".md", ".json")}
        again = dream.forget_sweep(db=str(self.db))
        self.assertEqual(again["swept"], 0)
        after = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and p.suffix in (".md", ".json")}
        self.assertEqual(before, after)

    def test_review_cases_payload_diary_formats_retries(self):
        """Review findings: diary text inside the payload, an untagged one-item list, "; " inside a fact,
        a log that could not be deleted (retried), a copy written back after the sweep."""
        dream.forget_sweep(db=str(self.db))
        semi = "Игорь любит чай; кофе не пьёт совсем после обеда"
        con = sqlite3.connect(self.db)
        con.execute("insert into facts (content) values (?)", (semi,))  # fact 4
        con.commit()
        con.close()
        dream.forget_sweep(db=str(self.db))
        legacy = "\n".join([
            "## Сон 2026-09-01",
            f"**Facts never used (1):** {FORGOTTEN_OLD[:45]}",  # one item, no tag, no "; "
            f"**Facts never used (2):** {semi[:45]}; {KEPT[:45]}",  # "; " inside the fact itself
            f"- {FORGOTTEN_NEW[:85]}",
            ""])
        self.path("memories/DREAMS.md").write_text(legacy, encoding="utf-8")
        payload = {"diary": legacy, "stats": {"facts": 4}}
        self.path("cache/dream.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        log = self.path("cron/output/job-dream/locked.md")
        log.write_text('{"fact_id": 2}', encoding="utf-8")
        self.forget(2, 3, 4)
        real_remove = os.remove

        def refuse(path):
            if str(path).endswith("locked.md"):
                raise PermissionError("busy")
            return real_remove(path)

        dream.os.remove = refuse
        try:
            first = dream.forget_sweep(db=str(self.db))
        finally:
            dream.os.remove = real_remove
        self.assertTrue(first["errors"])
        diary = self.path("memories/DREAMS.md").read_text(encoding="utf-8")
        payload = json.loads(self.path("cache/dream.json").read_text(encoding="utf-8"))
        for text in (diary, payload["diary"]):
            for gone in (FORGOTTEN_OLD[:30], FORGOTTEN_NEW[:30], "Игорь любит чай", "кофе не пьёт"):
                self.assertNotIn(gone, text)
            self.assertIn(KEPT[:30], text)
        second = dream.forget_sweep(db=str(self.db))  # the retry deletes the log now
        self.assertEqual((second["errors"], second["cron_logs"]), ([], 1))
        self.assertFalse(log.exists())
        # a pass that loaded the fact before the forget wrote the line back: the next run finds it
        with open(self.path("memories/DREAMS.md"), "a", encoding="utf-8") as f:
            f.write(f"- {FORGOTTEN_NEW[:85]} [#3]\n")
        self.assertEqual(dream.forget_sweep(db=str(self.db))["diary_lines"], 1)

    def test_result_is_scrubbed_before_it_is_published(self):
        """A fact forgotten while the pass ran never reaches its output."""
        self.forget(3)
        old = os.environ.get("DREAM_FACT_STORE")
        os.environ["DREAM_FACT_STORE"] = str(self.db)
        try:
            result = {"promotions": [{"fact_id": 3, "content": FORGOTTEN_NEW}, {"fact_id": 1, "content": KEPT}],
                       "diary": f"- {FORGOTTEN_NEW[:85]} [#3]\n- {KEPT[:85]} [#1]"}
            self.assertEqual(dream.scrub_result(result), 2)
        finally:
            if old is None:
                os.environ.pop("DREAM_FACT_STORE")
            else:
                os.environ["DREAM_FACT_STORE"] = old
        self.assertEqual([p["fact_id"] for p in result["promotions"]], [1])
        self.assertNotIn(FORGOTTEN_NEW[:30], result["diary"])

    def test_new_items_keep_one_line_and_unambiguous_lists(self):
        diary = dream.build_diary(14, 1, 0, [], [{"fact_id": 5, "content": "Чай; кофе\nи сок"}], [], [])
        self.assertIn("Чай, кофе и сок [#5]", diary)

    def test_new_diary_lines_carry_the_fact_id(self):
        diary = dream.build_diary(14, 2, 0, [{"fact_id": 7, "content": "Факт", "score": 0.7,
                                               "meta": {"mentions": 3, "ref_days": 2}}],
                                  [{"fact_id": 8, "content": "Другой факт"}], [], [],
                                  conflicts=[{"fact_id": 9, "content": "Третий"}])
        self.assertIn("Факт [#7]", diary)
        self.assertIn("Другой факт [#8]", diary)
        self.assertIn("Третий [#9]", diary)

    def test_fact_history_flag_and_old_stores(self):
        self.assertTrue(dream.fact_history_supported(str(self.db)))
        con = sqlite3.connect(self.db)
        con.execute("delete from meta")  # tables exist, the migration has not finished
        con.commit()
        con.close()
        self.assertFalse(dream.fact_history_supported(str(self.db)))
        old = self.home / "old.db"
        con = sqlite3.connect(old)
        con.execute("create table facts (fact_id integer primary key, content text)")
        con.commit()
        con.close()
        self.assertFalse(dream.fact_history_supported(str(old)))
        self.assertEqual(dream.load_forgotten(str(old)), {})

    def test_cli_and_the_no_agent_gate(self):
        env = dict(os.environ, HERMES_HOME=str(self.home), DREAM_SCRIPT=str(ROOT / "scripts" / "dream.py"),
                   DREAM_FACT_STORE=str(self.db), DREAM_FACT_SOURCE="holographic")
        (self.home / "dreaming.json").write_text(json.dumps({"fact_source": "holographic",
                                                             "fact_store_path": str(self.db)}), encoding="utf-8")
        gate = [sys.executable, str(ROOT / "scripts" / "dream-forget-sweep.py")]
        quiet = subprocess.run(gate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual((quiet.returncode, quiet.stdout), (0, ""), quiet.stderr)
        self.seed_copies()
        self.forget(2)
        loud = subprocess.run(gate, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(loud.returncode, 0, loud.stderr)
        self.assertIn("Forgotten facts cleaned", loud.stdout)
        self.assertNotIn(FORGOTTEN_OLD[:20], loud.stdout)
        cli = subprocess.run([sys.executable, str(ROOT / "scripts" / "dream.py"), "--hermes-home", str(self.home),
                              "--forget-sweep"], env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(json.loads(cli.stdout)["swept"], 0)

    def test_gate_does_not_wait_for_a_running_pass(self):
        try:
            import fcntl
        except ImportError:
            self.skipTest("POSIX locks only")
        env = dict(os.environ, HERMES_HOME=str(self.home), DREAM_SCRIPT=str(ROOT / "scripts" / "dream.py"),
                   DREAM_FACT_STORE=str(self.db), DREAM_FACT_SOURCE="holographic")
        self.seed_copies()
        self.forget(2)
        lock = self.home / "cache" / "dream-state.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            gate = subprocess.run([sys.executable, str(ROOT / "scripts" / "dream-forget-sweep.py")], env=env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=60)
            cli = subprocess.run([sys.executable, str(ROOT / "scripts" / "dream.py"), "--hermes-home",
                                  str(self.home), "--forget-sweep"], env=env, capture_output=True, text=True,
                                 encoding="utf-8", timeout=60)
            fcntl.flock(held, fcntl.LOCK_UN)
        self.assertEqual((gate.returncode, gate.stdout), (0, ""), gate.stderr)
        self.assertIn("skipped", json.loads(cli.stdout))
        self.assertIn(FORGOTTEN_OLD[:20], (self.home / "cache" / "dream.json").read_text(encoding="utf-8"))
        later = subprocess.run([sys.executable, str(ROOT / "scripts" / "dream-forget-sweep.py")], env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertIn("Forgotten facts cleaned", later.stdout)

    # Full Codex review of 2.7.2 (2026-10-14) -----------------------------------------------------
    def test_copies_without_their_own_id_are_found(self):
        """#4: the sweep knew facts by `fact_id` only. A memory entry written from the fact rode on — as
        another candidate's nearest entry and as a "still relevant?" question."""
        dream.forget_sweep(db=str(self.db))  # the index, while the facts exist
        payload = {"promotions": [{"fact_id": 1, "content": KEPT,
                                   "nearest_entry": {"entry": FORGOTTEN_NEW[:160], "full_entry": FORGOTTEN_NEW,
                                                     "old_text": FORGOTTEN_NEW}}],
                   "md_decays": [{"index": 3, "entry": FORGOTTEN_NEW}],
                   "conflicts": [{"fact_id": 1, "content": KEPT,
                                  "conflicts": [{"entry": f"Старое. {FORGOTTEN_NEW}"}]}]}
        self.path("cache/dream.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.forget(3)
        summary = dream.forget_sweep(db=str(self.db))
        self.assertEqual(summary["payload"], 3)
        text = self.path("cache/dream.json").read_text(encoding="utf-8")
        self.assertNotIn(FORGOTTEN_NEW[:30], text)
        payload = json.loads(text)
        self.assertEqual(payload["promotions"][0]["content"], KEPT, "the candidate itself stays")
        self.assertEqual(payload["promotions"][0]["nearest_entry"],
                         {"entry": dream.FORGOTTEN_COPY, "replace_unsafe": True}, "an anchor without its text is a blind replace")

    def test_an_item_carrying_a_forgotten_text_leaves_the_list(self):
        """Review of 2.7.4: a candidate whose own text is a forgotten fact stayed in `promotions` with a
        placeholder for its text, kept the gate open and offered "[removed: …]" for memory."""
        dream.forget_sweep(db=str(self.db))
        payload = {"promotions": [{"fact_id": 99, "content": FORGOTTEN_NEW}, {"fact_id": 1, "content": KEPT}],
                   "stats": {"promotions": 2}}
        self.path("cache/dream.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.forget(3)
        dream.forget_sweep(db=str(self.db))
        payload = json.loads(self.path("cache/dream.json").read_text(encoding="utf-8"))
        self.assertEqual([p["fact_id"] for p in payload["promotions"]], [1])

    def test_extraction_logs_are_found_old_and_new(self):
        """#5: `existing_facts` were bare strings, so an extraction log was never found by id."""
        dream.forget_sweep(db=str(self.db))
        (self.home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
            {"id": "job-dream", "script": "dream-precheck.py"},
            {"id": "job-extract", "script": "dream-extract-precheck.py"}]}), encoding="utf-8")
        folder = self.path("cron/output/job-extract")
        folder.mkdir(parents=True)
        legacy = {"existing_facts": [KEPT[:120], FORGOTTEN_NEW[:120]], "messages": [{"t": "x", "text": "y"}]}
        current = {"existing_facts": [{"fact_id": 1, "text": KEPT}, {"fact_id": 3, "text": FORGOTTEN_NEW}]}
        untouched = {"existing_facts": [KEPT[:120]]}
        for name, body in (("legacy.md", legacy), ("current.md", current), ("untouched.md", untouched)):
            (folder / name).write_text("prompt " + json.dumps(body, ensure_ascii=False), encoding="utf-8")
        self.forget(3)
        self.assertEqual(dream.forget_sweep(db=str(self.db))["cron_logs"], 2)
        self.assertEqual(os.listdir(folder), ["untouched.md"])

    def test_an_unreadable_store_is_an_error_not_a_clean_sweep(self):
        """#13: an unreadable shared store read as "nothing was forgotten" — the gate stayed silent."""
        self.db.write_bytes(b"not a database at all " * 20)
        summary = dream.forget_sweep(db=str(self.db))
        self.assertTrue(summary["errors"])
        self.assertIn(str(self.db), summary["errors"][0])


if __name__ == "__main__":
    unittest.main()
