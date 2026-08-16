#!/usr/bin/env python3
"""patch_cron_memory.py — let a cron job opt into memory (`allow_memory: true`).

Hermes starts every cron job with `skip_memory=True`, which keeps the memory
PROVIDER out of the session — so `fact_store` does not exist there and the
extraction job of this skill has nothing to call. (The nightly dream itself is
fine: the built-in file store is created whenever a job declares the `memory`
toolset.) This patch turns the constant into a per-job decision.

    python3 patch_cron_memory.py [<hermes-agent dir or scheduler.py>]
    python3 patch_cron_memory.py --check     # applied or not (exit 0 / 1)
    python3 patch_cron_memory.py --revert    # restore the upstream line

Idempotent, refuses to touch a file it does not recognise, byte-compiles the
result before writing and keeps a `.bak` next to the original. Re-run it after
a Hermes upgrade — an upgrade replaces the file.

Read docs/cron-memory.md before using this: you are widening what a scheduled
job may do to the user's memory, and the reason upstream keeps it closed is a
real one.
"""

from __future__ import annotations

import argparse
import os
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

MARKER = "# HERMES_DREAMING_CRON_MEMORY_OPT_IN"
OLD = "            skip_memory=True,  # Cron system prompts would corrupt user representations"
NEW = f'''            {MARKER}
            # Cron stays stateless by default.  A narrowly trusted maintenance
            # job (here: dreaming) may opt in with allow_memory=true.
            skip_memory=not bool(job.get("allow_memory", False)),'''


def _resolve(target):
    path = Path(target).expanduser()
    if path.is_dir():
        path = path / "cron" / "scheduler.py"
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Per-job memory opt-in for Hermes cron")
    ap.add_argument("target", nargs="?",
                    default=os.environ.get("HERMES_HOME", "~/.hermes") + "/hermes-agent",
                    help="hermes-agent directory or the path to cron/scheduler.py")
    ap.add_argument("--check", action="store_true", help="report state, change nothing")
    ap.add_argument("--revert", action="store_true", help="restore the upstream line")
    args = ap.parse_args(argv)

    path = _resolve(args.target)
    if not path.is_file():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 2
    source = path.read_text(encoding="utf-8")
    applied = MARKER in source

    if args.check:
        print(f"{'OK: applied' if applied else '-- not applied'}: {path}")
        return 0 if applied else 1

    if args.revert:
        if not applied:
            print(f"-- nothing to revert: {path}")
            return 0
        if NEW not in source:
            print("ERROR: the patched block was edited by hand — revert it yourself", file=sys.stderr)
            return 2
        updated = source.replace(NEW, OLD)
    else:
        if applied:
            print(f"OK: already applied: {path}")
            return 0
        if OLD not in source:
            print("ERROR: the upstream line was not found — this Hermes version differs from the one\n"
                  "  this patch was written for. Apply the change by hand (docs/cron-memory.md)\n"
                  f"  or check {path} for an existing per-job opt-in.", file=sys.stderr)
            return 2
        updated = source.replace(OLD, NEW, 1)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", delete=False) as tmp:
        tmp.write(updated)
        probe = Path(tmp.name)
    try:
        py_compile.compile(str(probe), doraise=True)
    except py_compile.PyCompileError as exc:
        probe.unlink(missing_ok=True)
        print(f"ERROR: result does not compile, nothing written: {exc}", file=sys.stderr)
        return 2
    probe.unlink(missing_ok=True)

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    print(f"{'OK: reverted' if args.revert else 'OK: applied'}: {path} (backup: {backup.name})")
    print("  restart the gateway for it to take effect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
