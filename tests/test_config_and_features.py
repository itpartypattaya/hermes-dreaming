"""Tests of the config layer and the features added in the 2026-08 review:
declarative alias rules, fail-closed trusted chats, Unicode tokenizer,
conflict (supersession) hints, nearest memory entry, memory-loss guard,
pinned entries, md_decays cap-before-mark, precheck config.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_dream import DreamFixture, dream, precheck  # noqa: E402  (same directory)


class ConfigTests(unittest.TestCase):
    """Config layer: defaults, deep merge, broken file, precedence, fail-closed chats."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dreaming.json"
        self._cfg = dream.CONFIG

    def tearDown(self):
        dream.configure(self._cfg)
        self.tmp.cleanup()

    def test_missing_config_gives_defaults(self):
        cfg = dream.load_config(str(self.path))
        self.assertEqual(cfg["gates"]["min_score"], 0.55)
        self.assertEqual(cfg["trusted_chat_ids"], [])

    def test_partial_config_is_deep_merged(self):
        self.path.write_text(json.dumps({"gates": {"min_score": 0.7}, "diary": {"heading": "## Сон"}}),
                             encoding="utf-8")
        cfg = dream.load_config(str(self.path))
        self.assertEqual(cfg["gates"]["min_score"], 0.7)
        self.assertEqual(cfg["gates"]["min_mentions"], 3)       # default survives
        self.assertEqual(cfg["diary"]["keep_sections"], 90)     # default survives
        self.assertEqual(cfg["diary"]["heading"], "## Сон")

    def test_broken_config_falls_back_to_defaults(self):
        self.path.write_text("{broken", encoding="utf-8")
        cfg = dream.load_config(str(self.path))
        self.assertEqual(cfg["gates"]["min_score"], 0.55)

    def test_env_overrides_config(self):
        self.path.write_text(json.dumps({"gates": {"min_score": 0.7}}), encoding="utf-8")
        os.environ["DREAM_MIN_SCORE"] = "0.9"
        try:
            dream.configure(dream.load_config(str(self.path)))
            self.assertEqual(dream.MIN_SCORE, 0.9)
        finally:
            del os.environ["DREAM_MIN_SCORE"]

    def test_configure_applies_diary_heading_and_timezone(self):
        dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG,
                                          {"diary": {"heading": "## Dream"}, "timezone": "UTC"}))
        real_now = dream._now
        dream._now = lambda: datetime(2026, 7, 10, 20, 30, tzinfo=timezone.utc)
        try:
            diary = dream.build_diary(14, 0, 0, [], [], [], [])
            self.assertTrue(diary.startswith("## Dream 2026-07-10"))
            self.assertTrue(dream.DIARY_SECTION_RE.match(diary))
        finally:
            dream._now = real_now

    def test_unknown_timezone_falls_back_to_utc(self):
        dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, {"timezone": "Mars/Olympus"}))
        self.assertEqual(dream.LOCAL_TZ, timezone.utc)

    def test_group_chats_fail_closed_without_list(self):
        dream.configure(dream.DEFAULT_CONFIG)
        self.assertFalse(dream._trusted_message("telegram", "group", "-100555"))
        self.assertTrue(dream._trusted_message("telegram", "dm", "42"))
        self.assertTrue(dream._trusted_message("telegram", None, None))
        self.assertFalse(dream._trusted_message("cron", None, None))
        self.assertFalse(dream._trusted_message("telegram", "supergroup", "-100555"))

    def test_agent_names_are_stopwords(self):
        dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, {"agent_names": ["Jarvis"]}))
        self.assertNotIn("jarvis", dream.sig_tokens("Jarvis, remind me about the dentist"))
        self.assertIn("dentist", dream.sig_tokens("Jarvis, remind me about the dentist"))


class TokenizerUnicodeTests(unittest.TestCase):
    def test_ukrainian_and_thai_are_not_invisible(self):
        toks = dream.sig_tokens("Сьогодні їдемо до бабусі в Київ")
        self.assertIn("сьогодні", toks)
        self.assertIn("бабусі", toks)
        self.assertTrue(dream.sig_tokens("พรุ่งนี้มีสอบคณิตศาสตร์"))

    def test_latin_min_len_and_digits(self):
        toks = dream.sig_tokens("gpt-5 vs the old api")
        self.assertIn("gpt-5", toks)
        self.assertNotIn("api", toks)  # 3 chars


class AliasRuleDslTests(unittest.TestCase):
    def test_and_of_or_groups(self):
        rules = [{"fact": [["cat", "dog"], ["food"]], "memory": [["pets"]]}]
        self.assertTrue(dream._semantic_memory_alias("Dog food is in the garage", "pets: garage", rules))
        self.assertFalse(dream._semantic_memory_alias("Dog leash is in the garage", "pets: garage", rules))
        self.assertFalse(dream._semantic_memory_alias("Dog food is in the garage", "no relation", rules))

    def test_number_term_needs_boundaries(self):
        rules = [{"fact": [["#42"]], "memory": [["#42"]]}]
        self.assertTrue(dream._semantic_memory_alias("thread 42 is for alerts", "42 alerts", rules))
        self.assertFalse(dream._semantic_memory_alias("thread 420 is for alerts", "42 alerts", rules))

    def test_no_rules_no_alias(self):
        self.assertFalse(dream._semantic_memory_alias("anything", "anything", []))


class ConflictTests(DreamFixture):
    """Same subject, different numbers → possible update of an existing entry."""

    OLD = "§\nУведомления для Ольги идут в ветку 42 рабочего чата.\n"

    def test_number_change_flagged(self):
        self.mem_md.write_text(self.OLD, encoding="utf-8")
        self.add_fact("Уведомления для Ольги теперь идут в ветку 437 рабочего чата", trust=0.6)
        result = self.run_dream()
        self.assertEqual(result["stats"]["conflicts"], 1)
        c = result["conflicts"][0]
        self.assertIn("437", c["conflicts"][0]["fact_numbers"])
        self.assertIn("42", c["conflicts"][0]["memory_numbers"])
        self.assertIn("nearest_entry", c)
        self.assertIsNotNone(precheck.compact_payload(result))

    def test_same_numbers_not_a_conflict(self):
        self.mem_md.write_text("§\nУведомления для Ольги идут в ветку 437 рабочего чата.\n",
                               encoding="utf-8")
        self.add_fact("Уведомления для Ольги всегда идут в ветку 437 рабочего чата", trust=0.6)
        self.assertEqual(self.run_dream()["stats"]["conflicts"], 0)

    def test_unrelated_numbers_not_a_conflict(self):
        self.mem_md.write_text(self.OLD, encoding="utf-8")
        self.add_fact("Тренировка по плаванию длится 45 минут в бассейне школы", trust=0.6)
        self.assertEqual(self.run_dream()["stats"]["conflicts"], 0)

    def test_conflict_shown_once_then_cooldown(self):
        self.mem_md.write_text(self.OLD, encoding="utf-8")
        self.add_fact("Уведомления для Ольги теперь идут в ветку 437 рабочего чата", trust=0.6)
        seen = self.home / "seen.json"
        first = self.run_dream(seen_state=str(seen))
        second = self.run_dream(seen_state=str(seen))
        self.assertEqual(first["stats"]["conflicts"], 1)
        self.assertEqual(second["stats"]["conflicts"], 0)
        self.assertEqual(second["stats"]["conflicts_suppressed"], 1)


class NearestEntryTests(unittest.TestCase):
    def test_nearest_entry_returned_for_related_fact(self):
        mem = ("§\nМарина ходит на утренние тренировки по вторникам в зале.\n"
               "§\nПроект Аврора переезжает на новый хостинг в сентябре.\n")
        near = dream.nearest_entry("Марина теперь ходит на утренние тренировки по четвергам в зале", mem)
        self.assertIsNotNone(near)
        self.assertIn("тренировки", near["entry"])

    def test_nearest_entry_none_for_unrelated(self):
        mem = "§\nПроект Аврора переезжает на новый хостинг в сентябре.\n"
        self.assertIsNone(dream.nearest_entry("Кошка спит на подоконнике каждое утро", mem))


class MemoryLossGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "memories").mkdir()
        self.mem = self.home / "memories" / "MEMORY.md"
        self.snap = self.home / "snapshot.json"
        self._old_home = dream.HOME
        dream.HOME = str(self.home)

    def tearDown(self):
        dream.HOME = self._old_home
        self.tmp.cleanup()

    def _entries(self, n):
        self.mem.write_text("".join(f"§\nЗапись номер {i} про что-то важное в доме.\n" for i in range(n)),
                            encoding="utf-8")

    def test_first_pass_only_snapshots(self):
        self._entries(8)
        self.assertEqual(dream.check_memory_loss(str(self.mem), str(self.snap)), [])
        self.assertTrue(self.snap.exists())

    def test_big_loss_alerts(self):
        self._entries(8)
        dream.check_memory_loss(str(self.mem), str(self.snap))
        self._entries(3)  # 5 of 8 gone
        alerts = dream.check_memory_loss(str(self.mem), str(self.snap))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["lost"], 5)
        self.assertEqual(alerts[0]["file"], "memories/MEMORY.md")

    def test_small_change_is_quiet(self):
        self._entries(8)
        dream.check_memory_loss(str(self.mem), str(self.snap))
        self._entries(7)
        self.assertEqual(dream.check_memory_loss(str(self.mem), str(self.snap)), [])

    def test_alert_wakes_agent(self):
        self.assertIsNotNone(precheck.compact_payload(
            {"stats": {"alerts": 1}, "alerts": [{"kind": "memory_loss"}]}))


class PinnedAndCapTests(unittest.TestCase):
    ENTRY = "Старинный граммофон хранится в кладовке на верхней полке слева."

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Path(self.tmp.name) / "MEMORY.md"
        self.state = Path(self.tmp.name) / "asked.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_pinned_entry_never_asked(self):
        self.mem.write_text(f"§\n📌 {self.ENTRY}\n", encoding="utf-8")
        self.assertEqual(dream.md_decays(str(self.mem), [], 60, str(self.state)), [])

    def test_unpinned_entry_asked(self):
        self.mem.write_text(f"§\n{self.ENTRY}\n", encoding="utf-8")
        self.assertEqual(len(dream.md_decays(str(self.mem), [], 60, str(self.state))), 1)

    def test_cap_marks_only_published_entries(self):
        self.mem.write_text("".join(f"§\nЗапись номер {i} про старинные вещи в кладовке дома.\n"
                                    for i in range(6)), encoding="utf-8")
        first = dream.md_decays(str(self.mem), [], 60, str(self.state), cap=4)
        self.assertEqual(len(first), 4)
        second = dream.md_decays(str(self.mem), [], 60, str(self.state), cap=4)
        # The two overflow entries were not marked as asked and come next.
        self.assertEqual(len(second), 2)


class PrecheckConfigTests(unittest.TestCase):
    def test_actionable_keys_from_config(self):
        self.assertIsNone(precheck.compact_payload({"stats": {"conflicts": 1}},
                                                   actionable_keys=("promotions",)))
        self.assertIsNotNone(precheck.compact_payload({"stats": {"conflicts": 1}}))

    def test_empty_sections_dropped_and_evidence_trimmed(self):
        payload = precheck.compact_payload({
            "stats": {"promotions": 1},
            "promotions": [{"fact_id": 1, "content": "x", "score": 0.7,
                            "evidence": [{"day": "2026-01-01", "text": "a" * 300}] * 3}],
            "quarantined": []})
        self.assertNotIn("quarantined", payload)
        self.assertEqual(len(payload["promotions"][0]["evidence"]), 2)
        self.assertEqual(len(payload["promotions"][0]["evidence"][0]["text"]), 100)


class ReplaceAnchorTests(unittest.TestCase):
    """`memory replace` matches old_text as a SUBSTRING of a live entry, so the
    anchor handed to the agent must be verbatim and unambiguous (live failure
    2026-08-16: paraphrased old_text → 4 zero-match errors → the core locked
    memory for the turn and nothing was written)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        (home / "memories").mkdir()
        self.mem = home / "memories" / "MEMORY.md"
        self.user = home / "memories" / "USER.md"
        self.mem.write_text(
            "Марина ходит на утренние тренировки по вторникам в зале у дома.\n"
            "§\nПроект Аврора переезжает на новый хостинг в сентябре 2026 года.\n",
            encoding="utf-8")
        self.user.write_text(
            "## Марина\nМарина любит жасминовый чай и не пьёт кофе после полудня.\n",
            encoding="utf-8")
        self._old_home = dream.HOME
        dream.HOME = str(home)

    def tearDown(self):
        dream.HOME = self._old_home
        self.tmp.cleanup()

    def _sources(self):
        return dream.load_durable_memory_sources(str(self.mem))

    def test_sources_only_cover_editable_targets(self):
        targets = {t for t, _ in self._sources()}
        self.assertEqual(targets, {"memory", "user"})

    def test_anchor_is_verbatim_substring_of_the_entry(self):
        near = dream.nearest_entry(
            "Марина теперь ходит на утренние тренировки по четвергам в зале у дома",
            sources=self._sources())
        self.assertEqual(near["target"], "memory")
        self.assertIn("old_text", near)
        # Exactly what the core does: `old_text in entry`.
        entries = self.mem.read_text(encoding="utf-8").split("\n§\n")
        self.assertTrue(any(near["old_text"] in e for e in entries), near["old_text"])

    def test_profile_candidate_points_at_user_target(self):
        near = dream.nearest_entry(
            "Марина любит жасминовый чай и отказывается от кофе после полудня, привычка устойчивая",
            sources=self._sources())
        self.assertEqual(near["target"], "user")

    def test_ambiguous_head_yields_no_anchor(self):
        # Two entries sharing the same first 60 characters: replace would hit
        # both, so no anchor is offered at all.
        self.mem.write_text(
            "Марина ходит на утренние тренировки по вторникам в зале у дома, вариант один.\n"
            "§\nМарина ходит на утренние тренировки по вторникам в зале у дома, вариант два.\n",
            encoding="utf-8")
        near = dream.nearest_entry("Марина ходит на утренние тренировки по четвергам в зале",
                                   sources=self._sources())
        self.assertNotIn("old_text", near)

    def test_no_nearest_entry_for_unrelated_candidate(self):
        self.assertIsNone(dream.nearest_entry("Тайский водитель называет пляж Sai Kaew",
                                              sources=self._sources()))


class SameSubjectDedupeTests(unittest.TestCase):
    """The entry the agent has just written from this candidate must not come
    back as a candidate (live case 2026-08-16: promoted at 17:02, returned in
    the next pass with forward coverage 0.57 — under the 0.62 threshold, and
    the entry too short for the reverse digest rule).

    The guard is numbers: identity counts only when the candidate introduces no
    number the entry lacks, so a superseding statement stays a conflict."""

    ENTRY = "С 2026-07-29 отдельная Telegram-ветка topic 9 для Даши неактуальна и не используется."
    FACT = ("2026-07-29 Виктор подтвердил, что отдельная Telegram-ветка topic 9 для Даши "
            "неактуальна и не должна использоваться.")

    def test_just_written_entry_is_recognised(self):
        self.assertTrue(dream.already_in_memory(self.FACT, "§\n" + self.ENTRY + "\n"))

    def test_neither_containment_rule_would_have_matched(self):
        # Insurance against "green for another reason": both older directions fail here.
        item = dream._stems(dream.sig_tokens(self.FACT))
        chunk = dream._stems(dream.sig_tokens(self.ENTRY))
        self.assertFalse(dream._covers(item, chunk))

    def test_new_number_is_never_absorbed_as_identity(self):
        # Same subject, but the fact carries a number the entry does not. The
        # identity rule must refuse it (the older fuzzy containment ignores
        # numbers entirely — which is exactly why `conflicts` is computed
        # independently of `in_memory` and still reports the pair).
        entry = "Уведомления для Ольги идут в ветку 42 рабочего чата."
        fact = "Уведомления для Ольги идут в ветку 437 рабочего чата."
        item = dream._stems(dream.sig_tokens(fact))
        chunk = dream._stems(dream.sig_tokens(entry))
        self.assertFalse(dream._same_subject(item, chunk, fact, entry))
        self.assertTrue(dream.find_conflicts(fact, "\u00a7\n" + entry + "\n"))

    def test_unrelated_entry_does_not_absorb_the_fact(self):
        entry = "Ольга — девушка Виктора, живёт с ним в Заречье; международный HR-рекрутер."
        fact = "2026-06-19 Виктор уточнил, что Даша любит жасминовый японский чай из ларька у дома."
        self.assertFalse(dream.already_in_memory(fact, "§\n" + entry + "\n"))

    def test_partial_overlap_is_not_identity(self):
        entry = "Марина ходит на утренние тренировки по вторникам в зале у дома рядом с парком."
        fact = ("Марина записалась на курс керамики по средам в мастерской возле школы, "
                "занятия вечерние.")
        self.assertFalse(dream.already_in_memory(fact, "§\n" + entry + "\n"))


if __name__ == "__main__":
    unittest.main()
