"""A throwaway Hermes home for the whole test run, set before any test module loads a script.

dream.py takes its home from HERMES_HOME (else ~/.hermes) when it is imported. A test that calls a function which
writes state (the forget sweep, a pass) on a copy of the module it did not point at a temporary home wrote into the
real one: on 2026-11-18 a new test put two of the dream's state files into %LOCALAPPDATA%\\hermes on a desktop, and
the same suite runs on a live install with ~/.hermes as the home. Every test module that loads a script imports this
module first (directly or through test_dream), so such a test writes here instead, and the directory goes when the
run ends. Tests that need a home of their own still make and point at one.
"""

import atexit
import os
import shutil
import tempfile

REAL_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
SANDBOX = tempfile.mkdtemp(prefix="hd-home-")
os.environ["HERMES_HOME"] = SANDBOX
atexit.register(shutil.rmtree, SANDBOX, True)
