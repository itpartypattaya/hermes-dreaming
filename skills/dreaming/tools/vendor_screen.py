#!/usr/bin/env python3
"""Rebuild scripts/holo_screen.py from the holographic memory provider's own screen.

The provider (itpartypattaya/hermes-plugin-holographic, MIT) refuses to store or recall a secret or an instruction
to the assistant, in all its languages; it never cleans what it already holds, nor people's messages. The dream
carries both into durable memory and its report, so it runs the same screen. The provider's `__init__.py` imports
Hermes itself and cannot be imported by a cron script, so the screen is copied: every top-level definition that
`_recall_problem` and `_secret_in` need, verbatim, with the comment block above it, in the source's order.
`tests/test_holographic_017.py` checks the copy against the provider when its source is at hand.

    python3 tools/vendor_screen.py <path to the provider's __init__.py> <version> <commit>
"""

import ast
import pathlib
import sys

ROOTS = ("_recall_problem", "_secret_in")
OUT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "holo_screen.py"


def _names(node):
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        out = []
        for t in node.targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
            elif isinstance(t, ast.Tuple):
                out += [e.id for e in t.elts if isinstance(e, ast.Name)]
        return out
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def definitions(src):
    """[(names, source with its comment block)] of the top-level definitions ROOTS need, in source order."""
    tree = ast.parse(src)
    by_name = {}
    for node in tree.body:
        for n in _names(node):
            by_name.setdefault(n, []).append(node)
    # A root the provider renamed or dropped gave a copy without it, and the tool reported success.
    missing = [r for r in ROOTS if r not in by_name]
    if missing:
        raise ValueError(f"{', '.join(missing)} not in the source — nothing copied; the screen changed, update ROOTS")
    need, stack = set(), list(ROOTS)
    while stack:
        n = stack.pop()
        if n in need or n not in by_name:
            continue
        need.add(n)
        for node in by_name[n]:
            stack += [s.id for s in ast.walk(node) if isinstance(s, ast.Name) and s.id in by_name]
    # A top-level statement other than a plain definition that touches a needed name ("_RE_CREDENTIALS += (…)", an
    # assignment under `if`, `obj.attr = …`) changes the screen without changing any definition: the copy would miss
    # it and the comparison would still pass (Codex review of 2.9.0). Refuse it; extend this tool when it appears.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)):
            if not isinstance(node, ast.Assign) or all(isinstance(t, (ast.Name, ast.Tuple)) for t in node.targets):
                continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # a docstring
        touched = {s.id for s in ast.walk(node) if isinstance(s, ast.Name)} & need
        if touched:
            raise ValueError(f"line {node.lineno}: a {type(node).__name__} touches {sorted(touched)} — not "
                             "copied by this tool")
    lines = src.splitlines()
    out = []
    for node in tree.body:
        names = _names(node)
        if not any(n in need for n in names):
            continue
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        out.append((names, "\n".join(lines[start:node.end_lineno])))
    return out


def main(argv):
    src_path, version, commit = argv
    src = pathlib.Path(src_path).read_text(encoding="utf-8")
    parts = definitions(src)
    head = f'''"""The holographic memory provider's screen for secrets and instructions to the assistant — a verbatim copy.

Source: itpartypattaya/hermes-plugin-holographic {version} (`{commit}`), `__init__.py`; MIT, (c) its authors.
Copied by tools/vendor_screen.py: every definition `_recall_problem` and `_secret_in` need, unchanged. Do not edit
here — run the tool again; tests/test_holographic_017.py compares this file with the provider's source.
"""
# flake8: noqa
# fmt: off
from __future__ import annotations

import re
import unicodedata

SOURCE_VERSION = "{version}"
SOURCE_COMMIT = "{commit}"
'''
    body = "\n\n\n".join(text for _, text in parts)
    OUT.write_text(head + "\n\n" + body + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(parts)} definitions from holographic {version}")


if __name__ == "__main__":
    main(sys.argv[1:])
