"""Codex review threads on PR #35 and PR #36, checked against 3.8.12.

- A generic fact source (`sqlite`, `jsonl`) that keeps its times as a basic ISO date ("20261117") read it as an epoch
  in seconds since 3.8.9: the fact was dated 1970-08-23 and looked 56 years old to recency and to "said again".
  Before 3.8.9 `datetime.fromisoformat` read it as the day it is, and the README says ISO times are read.
- A live fact whose text is how a longer forgotten fact begins (its first 45, 60 or 85 characters) went out of the
  payload with it: the forget sweep matched it by the forgotten fact's preview hashes although its own fingerprint
  says it is another fact. 3.8.8 added the 60-character preview a memory-loss alert names an entry by, so a live
  fact of exactly 60 characters started to go too (Codex review of PR #35).
"""

import json
import sqlite3
import unittest
from pathlib import Path

import test_fact_sources
import test_forget_sweep
from test_dream import dream

sweep_dream = test_forget_sweep.dream  # the copy of dream.py ForgetSweep sets HOME of


class BasicIsoDate(test_fact_sources.FactSourceFixture):

    def _load(self, created):
        path = Path(self.tmp.name) / "facts.jsonl"
        path.write_text(json.dumps({"id": "m-1", "content": "Marta drinks jasmine tea", "created_at": created},
                                   ensure_ascii=False) + "\n", encoding="utf-8")
        self.use(fact_source="jsonl", fact_store_path=str(path))
        return dream.load_facts()[0]

    def test_a_basic_iso_date_is_that_day(self):
        for created, want in (("20261117", "2026-11-17 00:00:00"), (" 20240229 ", "2024-02-29 00:00:00")):
            with self.subTest(created=created):
                self.assertEqual(self._load(created)["created_at"], want)

    def test_an_epoch_in_a_string_is_still_an_epoch(self):
        # 1763371800 = 2025-11-17 09:30:00 UTC; ten digits are no date, eight that are no date stay an epoch
        for created, want in (("1763371800", "2025-11-17 09:30:00"), ("1763371800000", "2025-11-17 09:30:00"),
                              ("20261317", "1970-08-23 12:08:37")):
            with self.subTest(created=created):
                self.assertEqual(self._load(created)["created_at"], want)


class ALiveFactIsNotTheStartOfAForgottenOne(unittest.TestCase):
    """The sweep's own store and home (ForgetSweep), two more facts: a live one and a forgotten one that begins with
    the live one's text."""

    _sweep = test_forget_sweep.ForgetSweep
    setUp, tearDown, path, forget = _sweep.setUp, _sweep.tearDown, _sweep.path, _sweep.forget

    CASES = {
        60: ("Anna prefers receiving weekly project status updates by mail",
             " except during her confidential medical leave next month."),
        45: ("Лео забирает Нину из школы по средам в четыре", " часа, а по пятницам её забирает бабушка Вера."),
        85: ("Лео забирает Нину из школы по средам и пятницам в пять часов, по вторникам её отводит",
             " бабушка Вера, пока Лео в командировке в Порту."),
    }

    def _add(self, *texts):
        con = sqlite3.connect(self.db)
        ids = [con.execute("insert into facts (content) values (?)", (t,)).lastrowid for t in texts]
        con.commit()
        con.close()
        return ids

    def test_the_sweep_keeps_the_live_fact_in_the_payload(self):
        for n, (live, tail) in self.CASES.items():
            with self.subTest(length=n):
                self.assertEqual(len(live), n, "the fixture: the live fact is exactly one preview long")
                gone = live + tail
                live_id, gone_id = self._add(live, gone)
                payload = {"promotions": [{"fact_id": live_id, "content": live}, {"fact_id": gone_id, "content": gone}],
                           "fact_decays": [{"fact_id": str(live_id), "content": live}]}
                self.path("cache/dream.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                sweep_dream.forget_sweep(db=str(self.db))  # the diary index, while both facts exist
                self.forget(gone_id)
                summary = sweep_dream.forget_sweep(db=str(self.db))
                self.assertEqual(summary["errors"], [])
                after = json.loads(self.path("cache/dream.json").read_text(encoding="utf-8"))
                self.assertEqual([p["fact_id"] for p in after["promotions"]], [live_id], "the forgotten one goes")
                self.assertEqual(after["promotions"][0]["content"], live)
                self.assertEqual([d["fact_id"] for d in after["fact_decays"]], [str(live_id)])

    def test_a_live_fact_carrying_the_forgotten_one_as_a_sentence_still_goes(self):
        # the rule the change keeps: a short item that holds a forgotten fact verbatim as one of its sentences
        gone = "Код от калитки знает только Вера."
        live_id, gone_id = self._add("Лео дома по средам. " + gone, gone)
        payload = {"promotions": [{"fact_id": live_id, "content": "Лео дома по средам. " + gone}]}
        self.path("cache/dream.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        sweep_dream.forget_sweep(db=str(self.db))
        self.forget(gone_id)
        sweep_dream.forget_sweep(db=str(self.db))
        after = json.loads(self.path("cache/dream.json").read_text(encoding="utf-8"))
        self.assertEqual(after["promotions"], [])


if __name__ == "__main__":
    unittest.main()
