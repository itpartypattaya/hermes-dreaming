#!/usr/bin/env python3
"""dream-forget-sweep.py — clean the dream's copies of facts the store has forgotten, without the model.

holographic >= 0.8.0 deletes a fact for good on `fact_store forget` and keeps a tombstone (its id, never its
text). The nightly pass cleans what this skill copied (cache/dream.json, the reject list, the cooldowns, the
diary and its archive, the dream cron jobs' logs) before it runs; this gate does the same in between, so a
"forget it" does not wait for the night.

Install: copy this file to `$HERMES_HOME/scripts/` (Hermes cron requires scripts to live there) and add a
`no_agent` cron job with `"script": "dream-forget-sweep.py"`, e.g. every 15 minutes. Contract (no_agent job):
nothing to do → empty stdout (no message); something cleaned → one line saying what; a failure → exit 1 with
the reason on stderr. Every run looks for every tombstone again, so a copy written back later is cleaned too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _hermes_home():
    """$HERMES_HOME, else the home this gate was installed into (`<home>/scripts/`), else ~/.hermes."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    if here.name == "scripts" and (here.parent / "config.yaml").is_file():
        return here.parent
    return Path(os.path.expanduser("~/.hermes"))


def find_dream(home):
    """dream.py of the installed skill: the plugin's copy first, then a regular skill install; `$DREAM_SCRIPT`
    overrides (the same lookup as dream-precheck.py)."""
    explicit = os.environ.get("DREAM_SCRIPT")
    if explicit:
        return Path(explicit)
    tail = Path("dreaming") / "scripts" / "dream.py"
    candidates = [home / "plugins" / "hermes-dreaming" / "skills" / tail,
                  home / "skills" / tail,
                  *sorted((home / "skills").glob("*/dreaming/scripts/dream.py"))]
    return next((c for c in candidates if c.is_file()), candidates[0])


def main():
    try:  # the summary line carries an emoji; a Windows console defaults to a legacy code page
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    home = _hermes_home()
    try:
        proc = subprocess.run([sys.executable, str(find_dream(home)), "--hermes-home", str(home), "--forget-sweep"],
                              capture_output=True, text=True, timeout=120)
        summary = json.loads((proc.stdout or "").strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[dream-forget-sweep] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if proc.returncode != 0 or summary.get("errors"):
        # What could not be cleaned stays found: the next run looks for every tombstone again.
        print(f"[dream-forget-sweep] not everything was cleaned: "
              f"{'; '.join(summary.get('errors') or []) or proc.stderr.strip()[-400:]}", file=sys.stderr)
        return 1
    parts = [f"{summary[k]} {label}" for k, label in (("payload", "payload items"), ("rejected", "reject records"),
                                                      ("seen", "cooldowns"), ("diary_lines", "diary lines"),
                                                      ("cron_logs", "cron logs"),
                                                      ("corrupt_copies", "set-aside broken state files"))
             if summary.get(k)]
    if parts:
        print(f"🧹 Forgotten facts cleaned from the dream's copies: {', '.join(parts)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
