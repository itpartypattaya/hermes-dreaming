#!/usr/bin/env python3
"""Rebuild scripts/holo_screen.py from the holographic memory provider's own screen.

The provider (itpartypattaya/hermes-plugin-holographic, MIT) refuses to store or recall a secret or an instruction
to the assistant, in all its languages; it never cleans what it already holds, nor people's messages. The dream
carries both into durable memory and its report, so it runs the same screen. Since holographic 0.18.0 the screen
lives in the provider's `screen.py`, which imports only the standard library, so the file is copied whole, as it is,
between a header and the version it came from. The rest of the provider imports Hermes itself and cannot be loaded
by a cron script, which has the standard library only. The tool refuses a source that imports a module outside
STDLIB, loads or reaches code by name at run time (DYNAMIC, a dunder attribute), lacks a name in ROOTS (the names the
dream reads), or does not load to its end in a Python without site-packages. The source is the provider's own screen:
these checks catch a mistake in it, they are no sandbox for a hostile file.
`tests/test_holographic_017.py` checks the copy against the provider when its source is at hand.

    python3 tools/vendor_screen.py <path to the provider's screen.py> <version> <commit>
"""

import ast
import os
import pathlib
import subprocess
import sys
import tempfile

# What dream.py reads from the copy (its _load_holo_screen checks the same names).
ROOTS = ("_recall_problem", "_secret_in", "_RE_CREDENTIALS", "_credential_matches")
# The modules a cron script loading the copy has: the standard library ones the screen uses. Another standard
# library module goes here when the screen starts to use it; the provider's own modules and Hermes never do.
STDLIB = ("__future__", "json", "re", "unicodedata")
# Names that load or run code by name, or reach the builtins that do, past the import check (Codex review:
# `__import__('hermes_cli')`, `globals()["__builtins__"]`, `from re import __builtins__`). An attribute with a dunder
# name (`().__class__.__base__…`) is refused too.
DYNAMIC = ("__builtins__", "__import__", "__loader__", "__spec__", "eval", "exec", "getattr", "globals", "locals",
           "vars")
LOAD_TIMEOUT = 60  # seconds for the trial load; the screen loads in well under one
OUT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "holo_screen.py"

HEAD = """\
# The holographic memory provider's screen for secrets and instructions to the assistant — a verbatim copy.
# Source: itpartypattaya/hermes-plugin-holographic {version} (`{commit}`), `screen.py`; MIT, (c) its authors.
# Copied whole by tools/vendor_screen.py. Do not edit here — run the tool again; tests/test_holographic_017.py
# compares this file with the provider's source.
# flake8: noqa
# fmt: off
"""
TAIL = """

SOURCE_VERSION = "{version}"
SOURCE_COMMIT = "{commit}"
"""
# Run in a separate Python without site-packages: load the copy by path, as dream.py does, and check ROOTS. The last
# line it prints says the module ran to its end: `raise SystemExit(0)` in the copy exits 0 too (Codex review).
_LOADED = "loaded to the end"
_LOAD = """\
import importlib.util, sys
spec = importlib.util.spec_from_file_location("_holo_screen_check", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
missing = [n for n in sys.argv[2:] if not hasattr(mod, n)]
if missing:
    sys.exit("no " + ", ".join(missing))
print(%r)
""" % _LOADED


def _defined(tree):
    """The names the module's top level binds for sure: definitions, plain assignments and imports, in order, less those
    `del` removes. Not one assigned under `if` or `try`, nor the container of `x[0] = …` or `x.attr = …` (Codex
    review)."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Delete):
            names -= {t.id for target in node.targets for t in ast.walk(target)
                      if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Del)}
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node, ast.AnnAssign) and node.value is None:
                continue
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                names |= {t.id for t in ast.walk(target) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def copy(source, version, commit):
    """The text of scripts/holo_screen.py made from `source`, the provider's screen.py."""
    tree = ast.parse(source)
    # A root the provider renamed or dropped gave a copy without it, and the tool reported success.
    missing = [r for r in ROOTS if r not in _defined(tree)]
    if missing:
        raise ValueError(f"{', '.join(missing)} not in the source — nothing copied; the screen changed, update ROOTS")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [part for alias in node.names for part in alias.name.split(".") + [alias.asname or ""]]
        else:
            names = []
        dynamic = [n for n in names if n in DYNAMIC]
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            dynamic.append(node.attr)
        if dynamic:
            raise ValueError(f"line {node.lineno}: {dynamic[0]} loads or reaches code by name, which a cron script "
                             "may not have — nothing copied")
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = ["." * node.level + (node.module or "")]
        else:
            continue
        for module in modules:
            if module.split(".")[0] not in STDLIB:
                raise ValueError(f"line {node.lineno}: imports {module}, which a cron script cannot load — nothing "
                                 "copied (a standard library module the screen starts to use goes into STDLIB)")
    if not source.endswith("\n"):
        source += "\n"
    text = HEAD.format(version=version, commit=commit) + source + TAIL.format(version=version, commit=commit)
    _load_alone(text)
    return text


def _load_alone(text):
    """Load the copy in a Python with the standard library only; ValueError with the reason when it does not. -X utf8:
    the reason in the child's own encoding (cp1251 on a Russian Windows) came out as question marks (Codex review)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "holo_screen.py")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        try:
            done = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", "-c", _LOAD, path, *ROOTS],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", cwd=tmp, timeout=LOAD_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError(f"the copy does not load: still running after {LOAD_TIMEOUT} s — nothing copied") from None
    if done.returncode or done.stdout.strip().splitlines()[-1:] != [_LOADED]:
        last = (done.stderr.strip().splitlines() or ["it stopped before its end, exit code %d" % done.returncode])[-1]
        raise ValueError(f"the copy does not load without the provider's package: {last} — nothing copied")


def main(argv):
    src_path, version, commit = argv
    text = copy(pathlib.Path(src_path).read_text(encoding="utf-8"), version, commit)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:  # Path.write_text has no newline= before 3.10
        f.write(text)
    print(f"{OUT}: holographic {version} screen.py, {len(text.splitlines())} lines")


if __name__ == "__main__":
    main(sys.argv[1:])
