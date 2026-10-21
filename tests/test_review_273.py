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

    def test_review_of_274_separators_and_plain_words(self):
        """Review of 2.7.4 (Antigravity): a hyphen separator was missed, so was an all-letter password after a
        verb of change and a colon, while a number after a dash in a story about the change was taken for one."""
        for text in ("Марина поменяла пароль от кабинета - Secret123!",
                     "Марина поменяла пароль от кабинета: розовыйслон",
                     "Пароль от сейфа – 77214"):
            self.assertEqual(dream.classify_unsafe(text), "secret", text)
        for text in ("Сбросил пароль от Zoom — 40-минутный лимит остался",
                     "Забыл пароль от домофона — кв.123 не открывает",
                     "Пароль от Wi-Fi сменили вчера вечером"):
            self.assertIsNone(dream.classify_unsafe(text), text)
        # 2.9.0: a Latin value after a dash is a password now, even in a story about the change (holographic's
        # screen; "— abc1" cannot be told from "— step1 completed", Codex code review of 2.9.0).
        for text in ("Поменял пароль от роутера — 5GHz сеть перезагрузилась",
                     "Changed password on server — step1 completed"):
            self.assertEqual(dream.classify_unsafe(text), "secret", text)


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
        self.assertEqual((hits[0]["fact_negations"], hits[0]["memory_negations"]), (["not tolerate"], []))

    def test_a_negation_elsewhere_in_the_entry_does_not_count(self):
        mem = "У хозяина байк Vespa Turbo; бензин и транспорт — не машина."
        fact = "У хозяина байк Vespa Turbo; транспортные расходы на бензин относятся к байку, не к машине."
        self.assertTrue(dream.already_in_memory(fact, mem))

    def test_the_same_negation_split_differently_still_matches(self):
        """The second draft compared sentence with sentence: a one-sentence rejection with "не" in its tail did not
        close the same fact split in two by ";" — on a live install a rejected fact came back. Negated words are
        compared now."""
        rejected = {"x": {"content": "Рабочий чат, ветка 256: переводы и адаптация школьных сообщений из LINE, "
                                     "если контекст не говорит иное.", "fingerprint": "-", "at": "2026-08-23"}}
        fact = ("Рабочий чат: thread 256 — переводы из школьного чата из Line; сообщения сюда трактовать как запрос "
                "перевести школьные сообщения, если контекст не говорит другое.")
        self.assertIsNone(dream.polarity_flip(fact, rejected["x"]["content"]))
        self.assertTrue(dream.is_rejected(fact, rejected))

    def test_the_same_negation_in_another_word_order_matches(self):
        """The third draft compared the sets of negated words as a whole: "не указывались дополнительные…" and
        "не указывать в клиентских…" say the same with different sets. On a live install: a false promotion."""
        entry = ("Марина предпочитает не указывать в клиентских версиях резюме дополнительную информацию, "
                 "желаемую зарплату и контакты кандидата.")
        fact = ("Марина предпочитает, чтобы в клиентских версиях резюме не указывались дополнительная информация, "
                "желаемая зарплата и контакты кандидата.")
        self.assertIsNone(dream.polarity_flip(fact, entry))
        self.assertTrue(dream.already_in_memory(fact, "§\n" + entry))

    def test_comparatives_negate_nothing(self):
        """Review of 2.7.4: "больше"/"more" counted as negations — "drinks more coffee" made a false conflict, and
        in "больше не ест" the comparative took the verb and the flip was lost."""
        self.assertIsNone(dream.polarity_flip("John usually drinks more coffee in office",
                                              "John usually drinks coffee in office"))
        self.assertEqual(dream.polarity_flip("Марина больше не ест мясо", "Марина больше ест мясо"),
                         (["не ест"], []))

    def test_a_shared_negation_does_not_hide_another_flip(self):
        """Review of 2.7.4: a shared "не ест мясо" masked the flip "не пьёт / пьёт молоко"."""
        self.assertEqual(dream.polarity_flip("Марина не ест мясо и не пьет молоко", "Марина не ест мясо и пьет молоко"),
                         (["не ест", "не пьет"], ["не ест"]))

    def test_a_long_profile_with_other_negations_is_no_conflict(self):
        """The first draft of the fix compared negations with every sentence of the entry that shared two words: on
        a live install a long profile without § entries produced three false "conflicts" and one false promotion."""
        mem = ("## Марина\nМарина — дизайнер, живёт у моря. Марина не работает по выходным дома. "
               "Не любит шумные кафе и громкую музыку.")
        fact = "Марина — дизайнер, живёт у моря и работает удалённо из дома."
        self.assertIsNone(dream.polarity_flip(fact, mem))
        self.assertEqual(dream.find_conflicts(fact, mem), [])


class UntrustedSourcesTests(unittest.TestCase):
    """#14: a list in dreaming.json replaced the default, and the shipped example listed `cron` alone —
    subagent and tool turns corroborated facts as if a person had said them."""

    def setUp(self):
        self._cfg = dream.SETTINGS.CONFIG

    def tearDown(self):
        dream.configure(self._cfg)

    def test_machine_sources_stay_untrusted_whatever_the_list(self):
        for listed in ([], ["cron"], ["cli"]):
            with self.subTest(listed=listed):
                dream.configure(dict(dream.SETTINGS.CONFIG, untrusted_sources=listed))
                self.assertLessEqual({"cron", "subagent", "tool"}, dream.SETTINGS.UNTRUSTED_SOURCES)
        self.assertIn("cli", dream.SETTINGS.UNTRUSTED_SOURCES)

    def test_the_shipped_example_lists_them(self):
        example = json.loads((ROOT / "examples" / "dreaming.example.json").read_text(encoding="utf-8"))
        self.assertLessEqual({"cron", "subagent", "tool"}, set(example["untrusted_sources"]))


class _HolographicFixture(DreamFixture):
    def setUp(self):
        super().setUp()
        self._cfg = dream.SETTINGS.CONFIG
        dream.configure(dict(dream.SETTINGS.CONFIG, fact_source="holographic"))

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
        saved = (dream.SETTINGS.KEYWORDS_ENABLED, dream.SETTINGS.KEYWORDS_CAP)
        dream.SETTINGS.KEYWORDS_ENABLED, dream.SETTINGS.KEYWORDS_CAP = True, 10
        try:
            self.add_fact("Марина ходит на утренние тренировки по вторникам в зале у дома", trust=0.9, days_old=30)
            seen = str(self.home / "seen.json")
            first = self.run_dream(seen_state=seen, ack=False)
            self.assertEqual(len(first["keywords_needed"]), 1)
            self.assertIsNone(precheck.compact_payload(first), "keywords alone do not wake the agent")
            second = self.run_dream(seen_state=seen, ack=False)
            self.assertEqual(len(second["keywords_needed"]), 1, "the agent never saw them — the rest is lifted")
            long_fact = "Марина ведёт дневник тренировок и записывает каждое занятие подробно. " * 6
            item = dream.keywords_needed_items([{"fact_id": 9, "content": long_fact, "meta": {"rc": 1},
                                                 "keywords": ""}], "holographic")
            # the cooldown key is the full text's, not that of the first 300 characters (review of 2.7.4)
            self.assertEqual(dream._feedback_key(item[0]), dream._fact_fingerprint(long_fact))
        finally:
            dream.SETTINGS.KEYWORDS_ENABLED, dream.SETTINGS.KEYWORDS_CAP = saved


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

    def test_no_write_without_the_lock(self):
        """Review of 2.7.4: without the lock (`held` = False) the write went ahead as if nothing happened."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "rejected.json")
            real = reject._dream()

            @contextlib.contextmanager
            def busy(home, wait=True):
                yield False

            class Spy:
                def __getattr__(self, name):
                    return getattr(real, name)
            spy = Spy()
            spy._sweep_lock = busy
            saved = reject._dream
            reject._dream = lambda: spy
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(reject.main(["--state", state, "--content", "Thread 9 is not needed any more"]), 1)
            finally:
                reject._dream = saved
            self.assertFalse(os.path.exists(state))

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


class CodexReview275Tests(unittest.TestCase):
    """A narrow Codex CLI review of the 2.7.2 → 2.7.5 diff (gpt-6.1-sol, 2026-10-15): six findings, each
    confirmed by a reproduction. The fingerprint scrub (no. 5) is in test_forget_sweep.py."""

    def test_an_unsafe_entry_shows_no_numbers_in_a_conflict(self):
        """No. 1: the text of an entry with a password was hidden, the password itself went out as
        `memory_numbers`."""
        hits = dream.find_conflicts("Марина водит машину Toyota Camry модели 2025 года",
                                    "Марина водит машину Toyota Camry модели 2024 года; пароль: 483726")
        self.assertEqual(hits, [{"entry": dream.ENTRY_REDACTED, "replace_unsafe": True, "fact_numbers": ["2025"]}])
        self.assertNotIn("483726", json.dumps(hits))
        hits = dream.find_conflicts("Марина водит машину Toyota Camry",
                                    "Марина не водит машину Toyota Camry; пароль от неё: розовыйслон")
        self.assertEqual(hits, [{"entry": dream.ENTRY_REDACTED, "replace_unsafe": True, "fact_negations": []}])

    def test_a_cyrillic_password_after_a_dash(self):
        """No. 2: any Cyrillic in the value made it "a word", so a password after a dash passed."""
        for text in ("Сменил пароль — Розовыйслон123!", "Сменил пароль - Кот2024!"):
            self.assertEqual(dream.classify_unsafe(text), "secret", text)
        for text in ("Сбросил пароль от Zoom — 40-минутный лимит остался",
                     "Забыл пароль от домофона — кв.123 не открывает"):
            self.assertIsNone(dream.classify_unsafe(text), text)

    def test_a_verbatim_substring_with_not_is_no_duplicate(self):
        """No. 3: a fact standing whole inside an entry with "не" counted as written before the negation
        check ran."""
        fact = "Использовать Telegram для рабочих уведомлений команды"
        memory = "Не использовать Telegram для рабочих уведомлений команды"
        self.assertFalse(dream.exact_or_alias_in_memory(fact, memory))
        self.assertFalse(dream.already_in_memory(fact, memory))
        self.assertEqual(len(dream.find_conflicts(fact, memory)), 1)
        self.assertTrue(dream.already_in_memory(fact, "Решили: " + fact))

    def test_a_word_said_both_ways_keeps_its_object(self):
        """No. 4: "likes peanuts but not almonds" against the reverse — both sides negate the same word."""
        self.assertEqual(dream.polarity_flip("Олег любит арахис, но не любит миндаль",
                                             "Олег не любит арахис, но любит миндаль"),
                         (["не любит миндаль"], ["не любит арахис"]))
        self.assertFalse(dream.already_in_memory("Олег любит арахис, но не любит миндаль",
                                                 "Олег не любит арахис, но любит миндаль"))
        self.assertIsNone(dream.polarity_flip("Олег любит арахис, но не любит миндаль",
                                              "Олег не любит миндаль"))
        self.assertIsNone(dream.polarity_flip("Олег любит арахис, но не любит миндаль",
                                              "Олег любит арахис и не любит миндаль"))

    def test_reject_takes_the_lock_of_the_given_home(self):
        """No. 6: with the dream on `--hermes-home`, the reject list took the lock of ~/.hermes."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "cache", "dream-rejected.json")
            real = reject._dream()
            homes = []

            @contextlib.contextmanager
            def lock(home, wait=True):
                homes.append(home)
                yield True

            class Spy:
                def __getattr__(self, name):
                    return getattr(real, name)
            spy = Spy()
            spy._sweep_lock = lock
            saved_dream, saved_home = reject._dream, reject.HOME
            reject._dream = lambda: spy
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(reject.main(["--hermes-home", tmp, "--state", state,
                                                  "--content", "Ветка 9 больше не нужна"]), 0)
            finally:
                reject._dream, reject.HOME = saved_dream, saved_home
            self.assertEqual(homes, [os.path.abspath(tmp)])
            self.assertEqual(spy.HOME, os.path.abspath(tmp))


class MetadataTests(unittest.TestCase):
    def test_skill_version_matches_the_plugin(self):
        manifest = ROOT.parents[1] / "plugin.json"
        if not manifest.is_file():
            self.skipTest("plugin.json lives in the public repository only")
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        self.assertIn(f"\nversion: {version}\n", (ROOT / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
