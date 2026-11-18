"""No test writes into a real Hermes home (home_guard.py).

A test that called the forget sweep on test_dream's copy of dream.py, without pointing it at a home of its own, wrote
the sweep state and the diary index into %LOCALAPPDATA%\\hermes on 2026-11-18; on a live install the same suite has
~/.hermes — the family's memory — as the default home.
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import test_dream  # first: the guard must come in through it, as it does for every module that imports it
import home_guard  # noqa: E402,I001

TESTS = Path(__file__).resolve().parent


class TestsKeepOutOfARealHome(unittest.TestCase):
    def test_the_run_has_a_throwaway_home(self):
        self.assertEqual(os.environ["HERMES_HOME"], home_guard.SANDBOX)
        self.assertNotEqual(os.path.normcase(home_guard.SANDBOX), os.path.normcase(home_guard.REAL_HOME))
        self.assertEqual(os.listdir(tempfile.gettempdir()).count(os.path.basename(home_guard.SANDBOX)), 1)

    def test_every_loaded_copy_of_dream_defaults_to_it(self):
        # test_dream's copy by name: a test may load dream.py again under the name "dream" and replace it in sys.modules.
        copies = {id(m): m for m in [test_dream.dream, *sys.modules.values()]
                  if hasattr(m, "forget_sweep") and hasattr(m, "HOME")}.values()
        # The sandbox, or a temporary home a test made and pointed a copy at (the scripts load their own copies of
        # dream.py, which keep the home of the test that loaded them last) — never the real one.
        real = os.path.normcase(os.path.realpath(home_guard.REAL_HOME))
        temp = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
        for mod in copies:
            with self.subTest(module=mod.__name__):
                home = os.path.normcase(os.path.realpath(mod.HOME))
                self.assertNotEqual(home, real)
                self.assertEqual(os.path.commonpath([home, temp]), temp, mod.HOME)

    def test_a_sweep_on_a_module_nobody_pointed_anywhere_writes_into_it(self):
        dream = test_dream.dream
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory_store.db")
            con = sqlite3.connect(db)
            con.executescript(
                "create table facts (fact_id integer primary key, content text, category text default 'general',"
                " tags text default '', trust_score real default 0.5, retrieval_count integer default 0,"
                " helpful_count integer default 0, created_at timestamp default current_timestamp,"
                " updated_at timestamp default current_timestamp);"
                "create table forgotten (fact_id integer primary key, forgotten_at text, digests text);"
                "insert into facts (content) values ('Кошку зовут Мурка, ей три года');"
                "insert into forgotten values (2, '2026-11-18 12:00:00', '[]');")
            con.commit()
            con.close()
            state = dream._state_default("DREAM_FORGET_STATE", "forget_sweep")
            self.assertTrue(state.startswith(home_guard.SANDBOX), state)
            try:
                dream.forget_sweep(db=db)
                self.assertTrue(os.path.exists(state), "the sweep wrote its state into the throwaway home")
            finally:
                for name in os.listdir(os.path.join(home_guard.SANDBOX, "cache")):
                    os.remove(os.path.join(home_guard.SANDBOX, "cache", name))

    def test_the_language_tests_leave_no_directory_behind(self):
        import test_languages
        home = os.path.normcase(os.path.realpath(test_languages.dream.HOME))
        sandbox = os.path.normcase(os.path.realpath(home_guard.SANDBOX))
        self.assertEqual(os.path.commonpath([home, sandbox]), sandbox, "made inside the throwaway home")

    def test_a_module_that_loads_a_script_by_path_imports_the_guard_first(self):
        """Directly, or through test_dream, which imports it before its own loads (checked here as well)."""
        load = re.compile(r"spec_from_file_location\(")
        guard = re.compile(r"^(?:import home_guard\b|import test_dream\b|from test_dream import)", re.M)
        for path in sorted(TESTS.glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            first = load.search(text)
            if not first:
                continue
            with self.subTest(file=path.name):
                found = guard.search(text, 0, first.start())
                if path.name == "test_dream.py":
                    found = found and found.group().startswith("import home_guard")
                self.assertTrue(found, "import home_guard (or test_dream) before the first script load")


if __name__ == "__main__":
    unittest.main()
