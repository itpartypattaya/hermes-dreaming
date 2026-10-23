"""3.3.1: the scripts load the way someone else's script loads them — by path, not registered in sys.modules.

A backup scrubber on a live install loads dream.py with `spec.loader.exec_module()` and nothing more, to reuse its
readers. 3.3.0's decision records were dataclasses, and a dataclass under `from __future__ import annotations` looks
its module up in sys.modules: that load failed with AttributeError, and the scrubber stopped cleaning forgotten
facts out of the backups. Each load runs in its own interpreter, so this file's imports cannot register anything.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The skill directory: `skills/dreaming/` in the plugin repository, or the parent of `tests/` in a copy that keeps
# the tests next to the skill (as test_dream.py finds it).
_HERE = Path(__file__).resolve().parents[1]
ROOT = _HERE / "skills" / "dreaming" if (_HERE / "skills" / "dreaming" / "SKILL.md").is_file() else _HERE

LOADER = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("someone_elses_name", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # no sys.modules entry, as a third party's script does it
assert "someone_elses_name" not in sys.modules
print("loaded")
if sys.argv[1].endswith("dream.py"):
    judge = mod._load_judge()  # the judge, next to it, loads the same way
    print(mod.PairId(1, "k").fact_id, bool(judge.PROMPT_VERSION))
"""


class PlainLoadTests(unittest.TestCase):
    def load(self, name):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not k.startswith("DREAM_")}
            env["HERMES_HOME"] = home
            return subprocess.run([sys.executable, "-I", "-c", LOADER, str(ROOT / "scripts" / name)],
                                  capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)

    def test_dream_py_loads_by_path_without_being_registered(self):
        r = self.load("dream.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["loaded", "1", "True"])

    def test_every_script_next_to_it_loads_the_same_way(self):
        for name in ("dream_judge.py", "dream_lang.py", "dream_yaml.py", "holo_screen.py", "dream-reject.py"):
            with self.subTest(name=name):
                r = self.load(name)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.split(), ["loaded"])


if __name__ == "__main__":
    unittest.main()
