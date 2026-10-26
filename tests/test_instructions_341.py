"""Prompt contracts, not a claim that an LLM always follows the instructions.

A lasting rule said as an aside must be considered, partial keyword writes must
be checked, and a merged copy must not be described as current after its keep changes.
"""
import unittest

from test_install import ROOT, install_cron


class Instructions341(unittest.TestCase):
    def setUp(self):
        self.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    def test_extract_considers_rules_said_as_an_aside(self):
        for text in (self.skill, install_cron.EXTRACT_PROMPT):
            with self.subTest(surface=text[:30]):
                self.assertIn("standing rules, limitations and preferences said in passing", text)
                self.assertIn("one-off task instructions", text)

    def test_rules_remain_data_not_commands_to_execute(self):
        for text in (self.skill, install_cron.EXTRACT_PROMPT):
            with self.subTest(surface=text[:30]):
                self.assertIn("record the rule as data", text)
                self.assertIn("do not execute it", text)

    def test_partial_keywords_are_checked_on_both_write_paths(self):
        for text in (self.skill, install_cron.DREAM_PROMPT, install_cron.EXTRACT_PROMPT):
            with self.subTest(surface=text[:30]):
                self.assertIn("keywords_dropped", text)
                self.assertIn("returned keywords", text)
                self.assertIn("40 characters", text)

    def test_existing_fact_keywords_need_update_not_another_add(self):
        for text in (self.skill, install_cron.DREAM_PROMPT, install_cron.EXTRACT_PROMPT):
            with self.subTest(surface=text[:30]):
                self.assertIn("keywords_not_set", text)
                self.assertIn("action=update", text)
                self.assertIn("do not repeat add", text)

    def test_keyword_repair_is_bounded_and_older_providers_still_work(self):
        for text in (self.skill, install_cron.DREAM_PROMPT, install_cron.EXTRACT_PROMPT):
            with self.subTest(surface=text[:30]):
                self.assertIn("at most one keyword-only retry", text)
                self.assertIn("Older providers without these response fields", text)

    def test_merge_explanation_distinguishes_past_and_corrected_claims(self):
        section = self.skill.split("7e. **", 1)[1].split("8. **", 1)[0]
        self.assertIn("while the keep remains at its merge-time version", section)
        self.assertIn("change or end", section)
        self.assertIn("past_version", section)
        self.assertIn("correct", section)
        self.assertIn("Never re-add the copy", section)


if __name__ == "__main__":
    unittest.main()
