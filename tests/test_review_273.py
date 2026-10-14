"""A full Codex CLI review of the 2.7.2 skill (gpt-6-astra, 2026-10-14): 14 findings, each confirmed by a reproduction.

Here are the ones that live in the pass, the gate and the reject list; the forget sweep (#4, #5, #13) is in
test_forget_sweep.py, the sender of an extraction message (#8) in test_extract_precheck.py.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import contextlib
import io
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from test_dream import DreamFixture, ROOT, dream, precheck, reject  # noqa: E402

SECRET = "Ключ доступа к панели: sk-abcdefghijklmnopqrstuvwxyz012345"


class PasswordEventTests(unittest.TestCase):
    """#6: "changed" or "forgot" anywhere earlier in the text switched password detection off entirely."""

    def test_an_event_word_does_not_hide_the_value(self):
        for text in ("Changed password: hunter2",
                     "I forgot the shopping list. password: hunter2",
                     "сменил пароль: hunter2",
                     "забыл список покупок. пароль: hunter2",
                     "Обновила роутер, пароль от wifi: Qw3rty!2026",
                     "Reset the router. New password: Xk9mP2qL",
                     "Пароль сменили — теперь длиннее. Пароль от сейфа: 9911"):
            self.assertEqual(dream.classify_unsafe(text), "secret", text)

    def test_an_event_without_a_value_is_still_an_event(self):
        for text in ("Марина сменила пароль от Wi-Fi — новый знает только она",
                     "Марина сменила пароль от Wi-Fi, новый знает только она",
                     "Пароль от почты обновили в сентябре",
                     "Забыл пароль от роутера, сбрасывали кнопкой"):
            self.assertIsNone(dream.classify_unsafe(text), text)


class PolarityTests(unittest.TestCase):
    """#7: a negation is a stopword for corroboration, and fuzzy dedupe took "does not eat" for "eats"."""

    PAIRS = (
        ("Oliver does tolerate roasted peanuts at dinner", "Oliver does not tolerate roasted peanuts at dinner"),
        ("Даша ест жареный арахис за ужином без проблем", "Даша не ест жареный арахис за ужином"),
        ("Даша любит ходить на балет по субботам в студию",
         "Даша больше не любит ходить на балет по субботам в студию"),
    )

    def test_the_opposite_statement_is_not_in_memory(self):
        for mem, fact in self.PAIRS:
            with self.subTest(fact=fact):
                self.assertTrue(dream.already_in_memory(mem, "§\n" + mem))
                self.assertFalse(dream.already_in_memory(fact, "§\n" + mem))

    def test_an_old_rejection_does_not_close_the_opposite_fact(self):
        for mem, fact in self.PAIRS:
            rejected = {"x": {"content": mem, "fingerprint": "-", "at": "2026-01-01"}}
            with self.subTest(fact=fact):
                self.assertTrue(dream.is_rejected(mem + " ", rejected))
                self.assertFalse(dream.is_rejected(fact, rejected))

    def test_it_is_offered_as_a_conflict(self):
        mem, fact = self.PAIRS[0]
        hits = dream.find_conflicts(fact, "§\n" + mem)
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]["fact_negations"], hits[0]["memory_negations"]), (["not"], []))

    def test_a_negation_elsewhere_in_the_entry_does_not_count(self):
        mem = "У хозяина байк Vespa Turbo; бензин и транспорт — не машина."
        fact = "У хозяина байк Vespa Turbo; транспортные расходы на бензин относятся к байку, не к машине."
        self.assertTrue(dream.already_in_memory(fact, mem))


class UntrustedSourcesTests(unittest.TestCase):
    """#14: a list in dreaming.json replaced the default, and the shipped example listed `cron` alone —
    subagent and tool turns corroborated facts as if a person had said them."""

    def setUp(self):
        self._cfg = dream.CONFIG

    def tearDown(self):
        dream.configure(self._cfg)

    def test_machine_sources_stay_untrusted_whatever_the_list(self):
        for listed in ([], ["cron"], ["cli"]):
            with self.subTest(listed=listed):
                dream.configure(dict(dream.CONFIG, untrusted_sources=listed))
                self.assertLessEqual({"cron", "subagent", "tool"}, dream.UNTRUSTED_SOURCES)
        self.assertIn("cli", dream.UNTRUSTED_SOURCES)

    def test_the_shipped_example_lists_them(self):
        example = json.loads((ROOT / "examples" / "dreaming.example.json").read_text(encoding="utf-8"))
        self.assertLessEqual({"cron", "subagent", "tool"}, set(example["untrusted_sources"]))


class _HolographicFixture(DreamFixture):
    def setUp(self):
        super().setUp()
        self._cfg = dream.CONFIG
        dream.configure(dict(dream.CONFIG, fact_source="holographic"))

    def tearDown(self):
        dream.configure(self._cfg)
        super().tearDown()

    def wake_with_a_promotion(self):
        self.add_fact("Марина предпочитает утренние тренировки по вторникам", trust=0.9, rc=2, helpful=2)
        for d in (1, 3, 5):
            self.add_message(f"Марина снова ходила на утренние тренировки по вторникам, день {d}", days_ago=d)


class LeakTests(_HolographicFixture):
    """#1, #2: texts the quarantine stops reached the agent around it."""

    def _rejected(self, content, days_ago=0, reason="key revoked"):
        path = self.home / "rejected.json"
        path.write_text(json.dumps({reject._fingerprint(content): {
            "fact_id": "1", "content": None, "redacted": "secret", "fingerprint": reject._fingerprint(content),
            "reason": reason,
            "at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")}},
            ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_a_rejected_secret_reaches_trust_feedback_without_its_text(self):
        self.add_fact(SECRET, trust=0.9)
        self.wake_with_a_promotion()
        result = self.run_dream(rejected_state=self._rejected(SECRET))
        self.assertEqual([i["content"] for i in result["trust_feedback"]], ["[hidden: secret]"])
        self.assertEqual(result["trust_feedback"][0]["call"]["fact_id"], 1, "the call needs the id only")
        compact = precheck.compact_payload(result)
        self.assertIsNotNone(compact)
        self.assertNotIn("sk-abcdef", json.dumps(compact, ensure_ascii=False))
        self.assertNotIn("sk-abcdef", json.dumps(result, ensure_ascii=False))

    def test_two_hidden_secrets_keep_their_own_cooldowns(self):
        other = "Пароль от роутера: Zx81!secret"
        self.add_fact(SECRET, trust=0.9)
        self.add_fact(other, trust=0.9)
        keys = {item["fingerprint"] for item in dream.trust_feedback_items(
            [{"fact_id": 1, "content": SECRET, "trust_score": 0.9, "unsafe": "secret"},
             {"fact_id": 2, "content": other, "trust_score": 0.9, "unsafe": "secret"}], {}, "holographic")}
        self.assertEqual(len(keys), 2, "the hidden text is the same, the fingerprints are not")

    def test_a_secret_in_the_nearest_entry_is_not_shown(self):
        self.mem_md.write_text("Марина ходит на утренние тренировки по вторникам в зал у дома. "
                               "Пароль от зала: hunter2secret\n", encoding="utf-8")
        self.add_fact("Марина предпочитает утренние тренировки по четвергам в зале", trust=0.9, rc=2, helpful=2)
        for d in (1, 3, 5):
            self.add_message(f"Снова ходила на утренние тренировки в зал, день {d}", days_ago=d)
        result = self.run_dream()
        self.assertNotIn("hunter2secret", json.dumps(result, ensure_ascii=False))
        near = [i["nearest_entry"] for s in ("promotions", "new_facts", "conflicts")
                for i in result[s] if i.get("nearest_entry")]
        self.assertTrue(near)
        for n in near:
            self.assertTrue(n["replace_unsafe"])
            self.assertNotIn("old_text", n)
            self.assertNotIn("full_entry", n)

    def test_a_secret_in_a_conflict_entry_is_not_shown(self):
        mem = "Оплата интернета 900 бат в месяц по карте Марины, пароль от кабинета: Qw3rty!2026"
        hits = dream.find_conflicts("Оплата интернета 1200 бат в месяц по карте Марины", "§\n" + mem)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["entry"], dream.ENTRY_REDACTED)


class GuardFieldsTests(_HolographicFixture):
    """#3: the gate compacted the payload and dropped the rule fields — an old rejection arrived as a plain promotion."""

    def test_previously_rejected_and_store_survive_compaction(self):
        fact = "Марина предпочитает утренние тренировки по вторникам"
        self.wake_with_a_promotion()
        path = self.home / "rejected.json"
        path.write_text(json.dumps({reject._fingerprint(fact): {
            "fact_id": "1", "content": fact, "fingerprint": reject._fingerprint(fact), "reason": "quit",
            "at": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")}},
            ensure_ascii=False), encoding="utf-8")
        result = self.run_dream(rejected_state=str(path))
        compact = precheck.compact_payload(result) or {}
        items = compact.get("promotions", []) + compact.get("new_facts", [])
        self.assertTrue(any(i.get("previously_rejected") for i in items), "the ask-first rule arrived")
        self.assertEqual(precheck.compact_fact({"fact_id": "k#1", "content": "x", "store": "k"})["store"], "k")


class RideAlongCooldownTests(_HolographicFixture):
    """#10: ride-along sections were put to rest even when the gate did not wake the agent."""

    def test_keywords_come_back_after_a_night_the_gate_kept_shut(self):
        saved = (dream.KEYWORDS_ENABLED, dream.KEYWORDS_CAP)
        dream.KEYWORDS_ENABLED, dream.KEYWORDS_CAP = True, 10
        try:
            self.add_fact("Марина ходит на утренние тренировки по вторникам в зале у дома", trust=0.9, days_old=30)
            seen = str(self.home / "seen.json")
            first = self.run_dream(seen_state=seen, ack=False)
            self.assertEqual(len(first["keywords_needed"]), 1)
            self.assertIsNone(precheck.compact_payload(first), "keywords alone do not wake the agent")
            second = self.run_dream(seen_state=seen, ack=False)
            self.assertEqual(len(second["keywords_needed"]), 1, "the agent never saw them — the rest is lifted")
        finally:
            dream.KEYWORDS_ENABLED, dream.KEYWORDS_CAP = saved


class QueueAlertTests(DreamFixture):
    """#11: the "already alerted" stamp was set after the state was written and never reached the file."""

    def test_the_alert_rests_between_runs(self):
        queue = self.home / "pending" / "memory"
        queue.mkdir(parents=True)
        for i in range(6):
            (queue / f"p{i}.json").write_text(json.dumps({
                "id": f"p{i}", "subsystem": "memory", "action": "add", "created_at": time.time() - 10 * 86400,
                "payload": {"action": "add", "target": "memory", "content": f"Запись {i} про дом"}},
                ensure_ascii=False), encoding="utf-8")
        seen = self.home / "seen.json"
        first = self.run_dream(seen_state=str(seen))
        self.assertTrue([a for a in first["alerts"] if a.get("kind") == "pending_writes"])
        self.assertIn("_pending_alert", json.loads(seen.read_text(encoding="utf-8")))
        second = self.run_dream(seen_state=str(seen))
        self.assertFalse([a for a in second["alerts"] if a.get("kind") == "pending_writes"])


class MissingFileTests(DreamFixture):
    """#12: a vanished file with one big entry raised no alert — the floor counted entries only."""

    def test_a_vanished_file_with_one_big_entry_alerts(self):
        snapshot = str(self.home / "snapshot.json")
        self.mem_md.write_text("Важная запись про семью и порядок дома. " * 50, encoding="utf-8")
        self.assertEqual(dream.check_memory_loss(str(self.mem_md), snapshot), [])
        self.mem_md.unlink()
        alerts = dream.check_memory_loss(str(self.mem_md), snapshot)
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"])


class RejectWriteTests(unittest.TestCase):
    """#9: the reject list was rewritten in place and without the shared lock — a write between another
    writer's read and write was lost, and a reader could catch an empty file."""

    def test_the_write_is_whole_and_under_the_state_lock(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "rejected.json")
            real = reject._dream()
            held = []

            @contextlib.contextmanager
            def lock(home, wait=True):
                held.append(True)
                try:
                    yield True
                finally:
                    held.pop()

            class Spy:
                def __getattr__(self, name):
                    return getattr(real, name)
            spy = Spy()
            spy._sweep_lock = lock
            saved_save, saved_dream = reject.save_state, reject._dream
            seen_lock = []

            def save(path, data):
                seen_lock.append(bool(held))
                saved_save(path, data)
            reject.save_state, reject._dream = save, (lambda: spy)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(reject.main(["--state", state, "--content", "Thread 9 is not needed any more",
                                                  "--reason", "outdated"]), 0)
            finally:
                reject.save_state, reject._dream = saved_save, saved_dream
            self.assertEqual(seen_lock, [True])
            self.assertEqual(sorted(os.listdir(tmp)), ["rejected.json"], "no temporary file is left")
            with open(state, encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)), 1)


class MetadataTests(unittest.TestCase):
    def test_skill_version_matches_the_plugin(self):
        manifest = ROOT.parents[1] / "plugin.json"
        if not manifest.is_file():
            self.skipTest("plugin.json lives in the public repository only")
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        self.assertIn(f"\nversion: {version}\n", (ROOT / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
