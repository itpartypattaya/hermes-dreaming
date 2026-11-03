"""Hermes 0.22 took `replace` out of the memory tool, and the night kept asking for it.

Since core v0.22.0 the memory tool offers add / patch / remove: `replace` answers "'replace' is not available; use
'patch' … Nothing was written", single or inside operations[]. `patch` changes one span: `old_text` is the exact text
to change and `content` its new text, the rest of the entry is kept; to rewrite a whole entry `old_text` is the whole
entry. The nightly prompt still taught "memory replace" with an anchor that only locates the entry — on 0.22 every
such edit was refused, and an anchor passed to patch together with a whole new entry would splice the entry into
itself.

Requirements:
- the nightly prompt asks only for actions core >= 0.22 accepts (add, patch, remove) and tells patch the way core
  does it; `replace` stays only as the fallback for a tool that has no patch (Hermes 0.21, which the dream supports);
- what the pass hands over next to a candidate (`nearest_entry`) speaks of patch, not of a replace that is gone;
- a write core staged for approval as a `patch` counts as waiting, like an add or a replace: its candidate is not
  offered again, and the entry it changes is not asked about;
- `install_cron.py --upgrade` (and a re-run) moves a job with 3.8.4's prompt to the new one.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

import test_install as support
from test_dream import dream  # noqa: E402
from test_install import FakeJobs, install_cron

OLD = json.loads((Path(__file__).resolve().parent / "fixtures" / "cron-prompts-384.json").read_text(encoding="utf-8"))
CORE_022_ACTIONS = {"add", "patch", "remove"}           # tools/memory_tool.py of v0.22.6, operations[].action.enum


def _sentences(text):
    return [s for s in re.split(r"(?<=[.:;])\s+", text) if s]


class ThePromptAsksForWhatCoreAccepts(unittest.TestCase):
    def test_every_memory_action_named_is_one_core_022_accepts(self):
        named = set(re.findall(r"\bmemory (add|patch|remove|replace|update|delete)\b", install_cron.DREAM_PROMPT))
        self.assertTrue(named, "the prompt names no memory action at all")
        self.assertLessEqual(named, CORE_022_ACTIONS, f"the prompt asks for {sorted(named - CORE_022_ACTIONS)}")
        self.assertIn("patch", named)

    def test_replace_is_only_the_fallback_for_a_tool_without_patch(self):
        uses = [s for s in _sentences(install_cron.DREAM_PROMPT)
                if re.search(r"\breplace\b", s.replace("replace_unsafe", ""))]
        for sentence in uses:
            self.assertRegex(sentence, r"no patch|without patch|before 0\.22",
                             f"replace outside the fallback for older Hermes: {sentence!r}")
        self.assertTrue(uses, "the fallback for Hermes 0.21 (replace, no patch) is gone")

    def test_patch_is_explained_the_way_core_applies_it(self):
        prompt = install_cron.DREAM_PROMPT
        self.assertRegex(prompt, r"old_text is the exact text to change")
        self.assertRegex(prompt, r"rest of the entry is kept")
        self.assertRegex(prompt, r"rewrite the entry as a whole, old_text is the whole full_entry")

    def test_failure_reports_name_the_actual_actions(self):
        self.assertNotIn("zero-match replace", install_cron.DREAM_PROMPT)
        self.assertNotIn("replace found no matching entry", install_cron.DREAM_PROMPT)


class WhatTheCandidateCarries(unittest.TestCase):
    ENTRY = "Masha (daughter, 7): ballet on Mondays at 17:30\nKsenia picks her up afterwards, Anton pays the studio"

    def test_a_multi_line_entry_is_explained_for_patch(self):
        near = dream.nearest_entry("Masha has ballet on Mondays at 17:30 and Ksenia picks her up",
                                   sources=[("memory", self.ENTRY), ("memory", "Server hr-box runs Debian 12")])
        self.assertTrue(near and near.get("multi_section"), near)
        self.assertIn("patch", near["note"])
        self.assertNotRegex(near["note"], r"^replace rewrites")

    def test_an_entry_too_long_to_show_is_not_to_be_edited_blind(self):
        long_entry = "Masha (daughter, 7): ballet on Mondays at 17:30. " + "Studio rules and notes. " * 60
        near = dream.nearest_entry("Masha has ballet on Mondays at 17:30 in the studio",
                                   sources=[("memory", long_entry)])
        self.assertTrue(near and near.get("replace_unsafe"), near)
        self.assertNotIn("do not replace", near["note"])
        self.assertIn("edit", near["note"])

    def test_an_entry_with_a_secret_is_not_to_be_edited(self):
        secret = "Router admin password: Tr0ub4dor&3xyz, the router for Masha's ballet studio is in the hall"
        near = dream.nearest_entry("The router of Masha's ballet studio is in the hall closet",
                                   sources=[("memory", secret)])
        self.assertTrue(near and near.get("replace_unsafe"), near)
        self.assertNotIn("do not replace", near["note"])
        self.assertIn("edit", near["note"])


class AStagedPatchIsWaiting(unittest.TestCase):
    """The record core 0.22.6 writes (`tools/write_approval.stage_write`) for a patch under write_approval."""

    ENTRY = "Masha (daughter, 7): ballet on Mondays at 17:30; Ksenia picks her up"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        pending = self.home / "pending" / "memory"
        pending.mkdir(parents=True)
        payload = {"action": "patch", "target": "memory", "content": "Mondays and Thursdays at 17:30",
                   "old_text": "Mondays at 17:30", "matched_entry": self.ENTRY}
        (pending / "a1b2c3d4.json").write_text(json.dumps({
            "id": "a1b2c3d4", "subsystem": "memory", "action": "patch",
            "summary": "patch in memory: entry matching: Mondays at 17:30", "origin": "foreground",
            "created_at": 1793000000.0, "payload": payload}), encoding="utf-8")
        self.pending = dream.load_pending_writes(str(self.home))

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_patched_entry_counts_as_staged_text(self):
        self.assertEqual([p["action"] for p in self.pending], ["patch"])
        staged = dream._staged_text(self.pending)
        self.assertIn("Masha (daughter, 7): ballet on Mondays and Thursdays at 17:30; Ksenia picks her up", staged)
        self.assertTrue(dream.already_in_memory("Masha has ballet on Mondays and Thursdays at 17:30", staged))

    def test_the_entry_it_changes_counts_as_going_away(self):
        self.assertEqual(dream._staged_removals(self.pending), [self.ENTRY])


class StoredJobs(FakeJobs):
    """A scheduler that keeps an update, so the installer's read-back sees it (as in test_cron_prompt_sync)."""

    def update_job(self, job_id, updates):
        super().update_job(job_id, updates)
        job = next(j for j in self.listed if j["id"] == job_id)
        job.update(updates)
        return dict(job)


class TheUpgradeMovesThe384Prompt(unittest.TestCase):
    setUp = support.InstallCronTests.setUp
    tearDown = support.InstallCronTests.tearDown
    _complete_install = support.InstallCronTests._complete_install
    _install_plugin = support.InstallCronTests._install_plugin
    _run_main = support.InstallCronTests._run_main

    def test_a_job_with_the_prompt_of_3_8_4_gets_the_new_one(self):
        self._complete_install()
        job = dict(id="night", script=install_cron.DREAM_SCRIPT, skills=["agent-plugin-hermes-dreaming-0a1b2c3d:dreaming"],
                   prompt=OLD["DREAM_PROMPT"], schedule={"kind": "cron", "expr": "0 3 * * *"}, enabled_toolsets=["memory"])
        jobs = StoredJobs([job])
        rc, out = self._run_main(jobs, "--extract-schedule", "", "--sweep-schedule", "", "--upgrade")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [("night", {"prompt": install_cron.DREAM_PROMPT})], out)
        self.assertEqual(job["prompt"], install_cron.DREAM_PROMPT)
        self.assertNotEqual(OLD["DREAM_PROMPT"], install_cron.DREAM_PROMPT)


if __name__ == "__main__":
    unittest.main()
