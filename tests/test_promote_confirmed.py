"""MEMORY.md takes what a person stands behind (F1 research of holographic, 2026-10-29, §7; approved 2026-10-30).

Requirements, in the user's words:
- A conclusion the agent drew and nobody confirmed (origin `inferred`) is never offered for MEMORY.md, however
  often it is recalled, marked helpful or its words come up in chat. When people did say it again, the night offers
  it for confirmation instead, and once a person confirmed it, it is promoted as before.
- What the person said or confirmed is promoted by the same corroboration rule as before.
- A store that keeps no origins (a provider before 0.15.0, another fact source) or a fact stored without one keeps
  today's behaviour: nothing silently stops being promoted there.

Run:  python -m unittest discover -s tests   (from the repository root)
"""
import contextlib
import io
import sqlite3
import unittest

from test_dream import DreamFixture, dream  # noqa: E402

USED = "Марина предпочитает утренние тренировки по вторникам"
OTHER = "Виктор любит зелёный чай без сахара по вечерам"


class _Holographic(DreamFixture):
    def setUp(self):
        super().setUp()
        self._cfg = dream.SETTINGS.CONFIG
        dream.configure(dict(dream.SETTINGS.CONFIG, fact_source="holographic"))

    def tearDown(self):
        dream.configure(self._cfg)
        super().tearDown()

    def history(self, origins, confirmed=(), with_origin=True):
        """The tables holographic keeps (0.8.0 versions; 0.15.0 origin column and confirmations), as dream.py reads
        them. with_origin=False: a store of 0.8–0.14, versions without the origin column."""
        con = sqlite3.connect(self.home / "memory_store.db")
        con.execute("CREATE TABLE fact_versions (version_id INTEGER PRIMARY KEY, fact_id INTEGER, status TEXT"
                    + (", src_origin TEXT)" if with_origin else ")"))
        if with_origin:
            con.execute("CREATE TABLE fact_confirmations (fact_id INTEGER, confirmed_at TEXT, session_id TEXT, "
                        "user_id TEXT, via TEXT)")
        for fid, origin in origins.items():
            if with_origin:
                con.execute("INSERT INTO fact_versions (fact_id, status, src_origin) VALUES (?, 'current', ?)",
                            (fid, origin))
            else:
                con.execute("INSERT INTO fact_versions (fact_id, status) VALUES (?, 'current')", (fid,))
        for fid in confirmed:
            con.execute("INSERT INTO fact_confirmations VALUES (?, '2026-10-01 10:00:00', 's2', 'u2', 'feedback')",
                        (fid,))
        con.commit()
        con.close()

    def used_often(self):
        """Two facts the agent recalled many times and marked helpful; nobody mentioned them in chat."""
        self.add_fact(USED, trust=0.9, rc=6, helpful=3, days_old=3)
        self.add_fact(OTHER, trust=0.9, rc=6, helpful=3, days_old=3)

    def promoted(self, result):
        return {p["fact_id"] for p in result["promotions"] + result.get("ephemeral_events", [])}


class InferenceIsNotPromoted(_Holographic):
    def test_an_inference_recalled_and_marked_helpful_is_not_promoted_what_was_said_is(self):
        self.used_often()
        self.history({1: "inferred", 2: "said"})
        self.assertEqual(self.promoted(self.run_dream()), {2})

    def test_an_inference_people_talk_about_is_offered_for_confirmation_not_promoted(self):
        self.add_fact(USED, trust=0.9, rc=2, helpful=2)
        for d in (1, 3, 5):
            self.add_message(f"Марина снова ходила на утренние тренировки по вторникам, день {d}", days_ago=d)
        self.history({1: "inferred"})
        result = self.run_dream()
        self.assertEqual(self.promoted(result), set())
        self.assertEqual([c["fact_id"] for c in result["confirm"]], [1])

    def test_once_a_person_confirmed_it_the_inference_is_promoted(self):
        self.used_often()
        self.history({1: "inferred", 2: "inferred"}, confirmed=(1,))
        self.assertEqual(self.promoted(self.run_dream()), {1})


class WithoutOriginsNothingChanges(_Holographic):
    def test_a_store_without_history_promotes_by_use(self):
        self.used_often()
        self.assertEqual(self.promoted(self.run_dream()), {1, 2})

    def test_a_store_without_the_origin_column_promotes_by_use(self):
        self.used_often()
        self.history({1: None, 2: None}, with_origin=False)
        self.assertEqual(self.promoted(self.run_dream()), {1, 2})

    def test_facts_stored_without_an_origin_promote_by_use(self):
        self.used_often()
        self.history({1: None, 2: "said"})
        self.assertEqual(self.promoted(self.run_dream()), {1, 2})


class ExplainSaysWhy(_Holographic):
    def test_explain_reports_an_inference_as_not_ready_and_a_said_fact_as_ready(self):
        self.used_often()
        self.history({1: "inferred", 2: "said"})
        for fact_id, ready in ((1, "False"), (2, "True")):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                dream.explain(fact_id, str(self.mem_md), 60)
            self.assertIn(f"promotion_ready {ready};", out.getvalue())
            if fact_id == 1:  # why: the origin, not missing corroboration (Codex review)
                self.assertIn("origin inferred: an agent's conclusion nobody confirmed is not promoted",
                              out.getvalue())
            else:
                self.assertIn("origin said", out.getvalue())


if __name__ == "__main__":
    unittest.main()
