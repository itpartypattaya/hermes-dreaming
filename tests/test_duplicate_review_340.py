"""3.4.0, part A after the code review: what the human is shown is what a "yes" merges, in the store that answers.

Synthetic household as in test_duplicate_night_340 (Виктор, Ольга, Лена, the agent Луна); no real memory is quoted.

Requirements, each with its tests:
- the text and the `version_id` of a side of a proposal come from one read of the store: a fact the provider changed
  after the pass loaded it is not put to the human (its old text with its new version would let a "yes" merge a
  text nobody saw);
- part A runs only when the pass reads the very database the provider (and so the session that answers) uses:
  `DREAM_FACT_STORE` / `fact_store_path` naming another file, a `db_path` the pass cannot resolve as the provider
  does, or a config whose scope cannot be read turn it off, with the reason in stats; the proposal names its store,
  a proposal of another store goes stale, and SKILL step 7e has the agent compare it with `fact_store stats`;
- a merge does not cost the forget sweep its means: after merge and a later forget of the group, diary lines without
  `[#id]` of the copy — its last text and older ones — are cleaned;
- `--stale` and a version that changed while the pair stayed the same let the question come back at once: the
  pair's cooldown and its `_pending` entry go; a forget sweep takes the pair out of `_pending` too;
- the candidate index finds what the rule finds over every pair, at any allowed `min_containment` (0 included);
- a "yes" through holographic's own merge with the proposal's `expect` merges exactly the texts shown, and refuses
  (`changed_since`) when a text changed — checked against the provider's code when it is at hand.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import importlib
import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from test_dream import ROOT, dream
from test_duplicate_night_340 import BRIDGE, CALLS, NightBase
from test_duplicate_pairs_340 import NOT_PAIRS, RETELLINGS


class ConsistentReadTests(NightBase):
    def test_a_text_changed_while_the_pass_ran_is_not_put_to_the_human(self):
        copy = self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        changed = BRIDGE[0].replace("служит", "работает")
        real = dream.load_inputs

        def provider_writes_after_the_load(*args, **kwargs):
            inputs = real(*args, **kwargs)
            self.change(copy, changed)  # holographic `change` between the pass's reads
            return inputs

        with mock.patch.object(dream, "load_inputs", provider_writes_after_the_load):
            result = self.night()
        for item in result.get("duplicate_pairs", []):
            for side in ("keep", "copy"):
                ((text,),) = self.sql("select content from fact_versions where version_id = ?",
                                      (item[side]["version_id"],))
                self.assertEqual(item[side]["content"], text, "the text shown is the text of the version given")
        self.assertNotIn("duplicate_pairs", result)
        self.assertEqual(self.stored() if self.proposals.exists() else {}, {})
        (item,) = self.night(days=1)["duplicate_pairs"]
        self.assertEqual((item["copy"]["content"], item["copy"]["version_id"]), (changed, self.version(copy)))


class StoreBindingTests(NightBase):
    def other_store(self):
        other = self.home / "elsewhere" / "memory_store.db"
        other.parent.mkdir()
        shutil.copy(self.db, other)
        return other

    def test_a_store_other_than_the_providers_turns_part_a_off(self):
        self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        other = self.other_store()  # the same ids and texts: `expect` could not tell the two apart
        with mock.patch.dict(os.environ, {"DREAM_FACT_STORE": str(other)}):
            result = self.night()
        self.assertNotIn("duplicate_pairs", result)
        self.assertEqual(result["stats"]["duplicate_pairs_skipped"], "store_mismatch")
        self.assertFalse(self.proposals.exists())

    def test_the_providers_store_named_another_way_is_the_same_store(self):
        self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        (self.home / "config.yaml").write_text(
            "memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n    db_path: $HERMES_HOME/memory_store.db\n",
            encoding="utf-8")
        with mock.patch.dict(os.environ, {"DREAM_FACT_STORE": str(self.home / "." / "memory_store.db")}):
            (item,) = self.night()["duplicate_pairs"]
        # What holographic's `fact_store stats` calls `database` for this home: the agent compares the two.
        self.assertEqual(self.stored()[item["proposal"]]["store"], str(self.home / "memory_store.db"))
        self.assertEqual(item["store"], str(self.home / "memory_store.db"))

    def test_a_db_path_the_pass_cannot_resolve_as_the_provider_does_turns_part_a_off(self):
        """A relative `db_path`: the provider opens it from the gateway's working directory, unknown here."""
        self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        (self.home / "config.yaml").write_text(
            "memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n    db_path: memory_store.db\n",
            encoding="utf-8")
        result = self.night()
        self.assertNotIn("duplicate_pairs", result)
        self.assertEqual(result["stats"]["duplicate_pairs_skipped"], "store_unknown")

    def test_an_unreadable_config_is_an_unknown_scope(self):
        self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        (self.home / "config.yaml").write_bytes(
            b"memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n    scope: chat # \xff\n")
        with mock.patch.dict(os.environ, {"DREAM_FACT_STORE": str(self.db)}):
            result = self.night()
        self.assertNotIn("duplicate_pairs", result)
        self.assertEqual(result["stats"]["duplicate_pairs_skipped"], "scope_unknown")

    def test_a_proposal_of_another_store_goes_stale(self):
        self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        self.night()
        other = self.other_store()
        (self.home / "config.yaml").write_text(
            "memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n"
            f"    db_path: {other.as_posix()}\n", encoding="utf-8")
        self.night(days=1)
        rec = self.stored()["D-1"]
        self.assertEqual(rec["status"], "stale", "its ids and versions are another database's")

    def test_step_7e_has_the_agent_check_the_store_before_a_merge(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        step = " ".join(text.split("\n7e. ", 1)[1].split("\n8. ", 1)[0].split())
        self.assertIn("`fact_store stats`", step)
        self.assertIn("its `database` must be the proposal's `store`", step)
        self.assertIn("no `database` to compare → stop", step)


class MergeThenForgetTests(NightBase):
    def test_a_forget_after_a_merge_cleans_untagged_lines_of_the_copy_and_its_older_texts(self):
        older = BRIDGE[0].replace("служит", "работает")
        copy = self.fact(older)
        keep = self.fact(BRIDGE[1])
        dream.forget_sweep()  # each pass indexes the facts first: the copy's first text
        self.change(copy, BRIDGE[0])
        dream.forget_sweep()  # and its second
        other = "Лена ходит на балет по вторникам и четвергам после школы."
        diary = self.home / "memories" / "DREAMS.md"
        diary.write_text(f"## Сон 2026-10-20\n- {older[:85]}\n- {other}\n## Сон 2026-10-24\n- {BRIDGE[0][:85]}\n"
                         f"- {BRIDGE[1][:85]}\n", encoding="utf-8")
        self.merge(keep, copy)
        dream.merge_sweep()
        self.assertIn(older[:85], diary.read_text(encoding="utf-8"), "a merge is no forget: the diary stays")
        self.forget(keep, copy)  # the provider: a tombstone for the keep and each of its copies
        swept = dream.forget_sweep()
        text = diary.read_text(encoding="utf-8")
        self.assertNotIn(older[:85], text)
        self.assertNotIn(BRIDGE[0][:85], text)
        self.assertNotIn(BRIDGE[1][:85], text)
        self.assertIn(other, text)
        self.assertEqual(swept["diary_lines"], 3)


class CooldownReleaseTests(NightBase):
    def pending_pairs(self):
        return ((self.seen_state().get("_pending") or {}).get("seen") or {}).get("duplicate_pairs") or []

    def test_stale_lets_the_question_come_back_at_once(self):
        """The merge failed without a change of text: `--stale D-1`, and the next night asks again."""
        self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        self.night()
        key = self.stored()["D-1"]["pair_key"]
        self.assertIn(key, self.pending_pairs())
        self.assertEqual(self.reject("--stale", "D-1")[0], 0)
        self.assertNotIn(key, self.seen_state()["duplicate_pairs"], "the cooldown of D-1 is gone")
        self.assertNotIn(key, self.pending_pairs())
        (item,) = self.night(days=1)["duplicate_pairs"]
        self.assertEqual(item["proposal"], "D-2")

    def test_stale_leaves_the_cooldown_of_other_pairs(self):
        self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        self.fact(CALLS[0])
        self.fact(CALLS[1])
        self.night()
        self.night(days=1)
        other = self.stored()["D-2"]["pair_key"]
        self.assertEqual(self.reject("--stale", "D-1")[0], 0)
        self.assertIn(other, self.seen_state()["duplicate_pairs"])

    def test_a_new_version_of_the_same_text_asks_again_at_once(self):
        """An edit of case only keeps the pair's key; the proposal goes stale, and its rest goes with it."""
        copy = self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        self.night()
        self.change(copy, BRIDGE[0].replace("Ветка", "ветка"))
        self.assertEqual(dream.pair_key(BRIDGE[0], BRIDGE[1]),
                         dream.pair_key(BRIDGE[0].replace("Ветка", "ветка"), BRIDGE[1]))
        result = self.night(days=1)
        self.assertEqual(self.stored()["D-1"]["status"], "stale")
        (item,) = result["duplicate_pairs"]
        self.assertEqual((item["proposal"], item["copy"]["version_id"]), ("D-2", self.version(copy)))

    def test_a_forget_sweep_takes_the_pair_out_of_pending(self):
        copy = self.fact(BRIDGE[0])
        self.fact(BRIDGE[1])
        self.night()
        key = self.stored()["D-1"]["pair_key"]
        self.assertIn(key, self.pending_pairs())
        self.forget(copy)
        dream.forget_sweep()
        self.assertNotIn(key, self.pending_pairs())
        self.assertNotIn(key, self.seen_state()["duplicate_pairs"])


class IndexAtEveryThresholdTests(unittest.TestCase):
    TEXTS = ([t for case in RETELLINGS for t in case[1:]] + [t for case in NOT_PAIRS for t in case[1:3]] + [
        "alpha bravo cedar delta", "alpha ember frost grove", "kilo lima mike november",
        "Виктор любит кофе по утрам дома.", "Ольга пьёт чай вечером на веранде.", "@viktor_k — Виктор", "4410"])

    def facts(self):
        return [{"fact_id": i + 1, "content": t} for i, t in enumerate(self.TEXTS)]

    def test_the_index_and_a_full_scan_agree_at_every_allowed_threshold(self):
        facts = self.facts()
        for floor in (0.0, 0.1, 0.2, 0.25, 0.34, 0.5, 0.7, 0.9, 1.0):
            with self.subTest(min_containment=floor), mock.patch.object(dream.SETTINGS, "DUP_MIN_CONTAINMENT", floor):
                found, _checks = dream.find_duplicate_pairs(facts)
                brute = {(a["fact_id"], b["fact_id"]) for n, a in enumerate(facts) for b in facts[n + 1:]
                         if dream.duplicate_pair(a["content"], b["content"])[0]}
                self.assertEqual({(p["a"], p["b"]) for p in found}, brute)

    def test_one_shared_word_of_four_is_found_at_a_low_threshold(self):
        facts = [{"fact_id": 1, "content": "alpha bravo cedar delta"},
                 {"fact_id": 2, "content": "alpha ember frost grove"}]
        with mock.patch.object(dream.SETTINGS, "DUP_MIN_CONTAINMENT", 0.2):
            self.assertTrue(dream.duplicate_pair(facts[0]["content"], facts[1]["content"])[0])
            found, _ = dream.find_duplicate_pairs(facts)
        self.assertEqual([(p["a"], p["b"]) for p in found], [(1, 2)])

    def test_a_zero_threshold_compares_every_pair_within_the_budget(self):
        facts = self.facts()
        with mock.patch.object(dream.SETTINGS, "DUP_MIN_CONTAINMENT", 0.0):
            _found, checks = dream.find_duplicate_pairs(facts)
            with self.assertRaises(dream.DupBudget):
                dream.find_duplicate_pairs(facts, max_checks=checks - 1)
            dream.find_duplicate_pairs(facts, max_checks=checks)


def _provider_dir():
    """A holographic source with `merge` (0.19.0): HOLOGRAPHIC_PROVIDER (its directory), else the directory of
    HOLOGRAPHIC_SRC when that one has `merge_facts`. HOLOGRAPHIC_PROVIDER, when set, must be such a source."""
    explicit = os.environ.get("HOLOGRAPHIC_PROVIDER")
    if explicit:
        if "def merge_facts" not in _read(Path(explicit) / "history.py"):
            raise FileNotFoundError(f"HOLOGRAPHIC_PROVIDER={explicit} is no holographic >= 0.19.0 (no merge_facts)")
        return Path(explicit)
    src = os.environ.get("HOLOGRAPHIC_SRC")
    if src and "def merge_facts" in _read(Path(src).parent / "history.py"):
        return Path(src).parent
    return None


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class ProviderMergeTests(NightBase):
    """The proposal's `expect` against holographic's own `merge_facts` (the code `fact_store merge` calls). The
    store module is loaded without the plugin's `__init__` (which needs Hermes core); the core's WAL helper, which
    only sets the journal mode, is stood in for."""

    @classmethod
    def setUpClass(cls):
        where = _provider_dir()
        if where is None:
            raise unittest.SkipTest("no holographic >= 0.19.0 at hand: set HOLOGRAPHIC_PROVIDER to its directory")
        cls._saved = {k: sys.modules.get(k) for k in ("hermes_state_wal", "_holo_provider")}
        wal = types.ModuleType("hermes_state_wal")
        wal.apply_wal_with_fallback = lambda conn, db_label=None: conn.execute("PRAGMA journal_mode=WAL")
        sys.modules.setdefault("hermes_state_wal", wal)
        pkg = types.ModuleType("_holo_provider")
        pkg.__path__ = [str(where)]
        sys.modules["_holo_provider"] = pkg
        cls.store_mod = importlib.import_module("_holo_provider.store")
        cls.history = importlib.import_module("_holo_provider.history")

    @classmethod
    def tearDownClass(cls):
        for name in [m for m in sys.modules if m.startswith("_holo_provider")]:
            del sys.modules[name]
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)

    def setUp(self):
        super().setUp()
        self.db.unlink()  # the fixture's tables: the provider makes its own
        self.store = self.store_mod.MemoryStore(db_path=str(self.db), hrr_dim=64)

    def tearDown(self):
        self.store.close()
        super().tearDown()

    def merge_as_step_7e(self, rec):
        expect = {str(rec["keep"]["fact_id"]): rec["keep"]["version_id"],
                  str(rec["copy"]["fact_id"]): rec["copy"]["version_id"]}
        return self.store.merge_facts(rec["keep"]["fact_id"], [rec["copy"]["fact_id"]], expect=expect)

    def test_a_yes_merges_exactly_the_texts_shown_and_a_changed_text_is_refused(self):
        copy = self.store.add_fact(BRIDGE[0])
        keep = self.store.add_fact(BRIDGE[1])
        self.night()
        first = self.stored()["D-1"]
        self.assertEqual((first["keep"]["fact_id"], first["copy"]["fact_id"]), (keep, copy))
        changed = BRIDGE[0].replace("служит", "работает")
        self.store.change_fact(copy, changed)
        with self.assertRaises(self.history.ChangedSince) as refused:
            self.merge_as_step_7e(first)
        self.assertIn(copy, refused.exception.current)
        self.assertEqual(self.sql("select count(*) from fact_merges")[0][0], 0, "nothing written")
        self.assertEqual(sorted(r[0] for r in self.sql("select fact_id from facts")), sorted([copy, keep]))

        self.night(days=1)
        stored = self.stored()
        self.assertEqual(stored["D-1"]["status"], "stale")
        second = stored["D-2"]
        self.assertEqual(second["copy"]["content"], changed)
        done = self.merge_as_step_7e(second)
        self.assertEqual(done["merged"], [copy])
        ((merged_text,),) = self.sql("select content from fact_versions where fact_id = ? and status = 'merged'",
                                     (copy,))
        self.assertEqual(merged_text, second["copy"]["content"], "the copy merged is the text shown")
        ((keep_text,),) = self.sql("select content from facts where fact_id = ?", (keep,))
        self.assertEqual(keep_text, second["keep"]["content"])
        self.assertEqual(self.merge_as_step_7e(second)["already"], [copy], "a lost answer, asked again: no change")
        self.night(days=2)
        self.assertEqual(self.stored()["D-2"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
