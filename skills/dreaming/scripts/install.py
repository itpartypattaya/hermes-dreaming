#!/usr/bin/env python3
"""install.py — put the cron gates where Hermes can run them, and verify the install.

Hermes only executes cron scripts that physically live in $HERMES_HOME/scripts
(the scheduler resolves the path and refuses anything outside that directory —
a symlink does not help). So the two pre-check scripts must be COPIED there,
which means they can silently drift from the skill after an update. `--check`
is the guard against exactly that.

    python3 install.py            copy the gates, seed a config if absent, then verify
    python3 install.py --check    verify only, change nothing (exit 1 on any problem)

Everything is idempotent: re-running is safe. Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import filecmp
import importlib.util
import io
import json
import os
import re
import shutil
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
GATES = ("dream-precheck.py", "dream-extract-precheck.py", "dream-forget-sweep.py")
# Gates of jobs that load the skill (the forget sweep is a no_agent job: no skill, no model).
SKILL_GATES = ("dream-precheck.py", "dream-extract-precheck.py")
EXAMPLE = SKILL_DIR / "examples" / "dreaming.example.json"


class Report:
    def __init__(self):
        self.ok_n = self.warn_n = self.bad_n = 0

    def ok(self, msg):
        print(f"  ✓ {msg}")
        self.ok_n += 1

    def warn(self, msg):
        print(f"  ! {msg}")
        self.warn_n += 1

    def bad(self, msg):
        print(f"  ✗ {msg}")
        self.bad_n += 1


def _load_dream(home, cfg_path):
    """dream.py of this skill, configured for `home` — without touching the
    process environment."""
    spec = importlib.util.spec_from_file_location("_dream_for_install", SKILL_DIR / "scripts" / "dream.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HOME = str(home)
    mod.configure(mod.load_config(str(cfg_path)))
    return mod


def check_gates(home, check_only, r):
    scripts = home / "scripts"
    if not check_only:
        scripts.mkdir(parents=True, exist_ok=True)
        for g in GATES:
            shutil.copyfile(SKILL_DIR / "scripts" / g, scripts / g)
            os.chmod(scripts / g, 0o755)
    for g in GATES:
        dst = scripts / g
        if not dst.is_file():
            r.bad(f"{g} is not in {scripts} — cron cannot run it (run install.py)")
        elif not filecmp.cmp(SKILL_DIR / "scripts" / g, dst, shallow=False):
            r.bad(f"{g} in {scripts} differs from the skill copy — re-run install.py")
        else:
            r.ok(f"{g} in place and identical to the skill copy")


def check_config(cfg, check_only, r):
    if cfg.is_file():
        try:
            json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r.bad(f"config {cfg} is not valid JSON — the pass would fall back to defaults")
            return
        if filecmp.cmp(cfg, EXAMPLE, shallow=False):
            # A seeded-but-unedited config is the most common half-install:
            # everything runs, but with someone else's timezone and a placeholder chat.
            r.warn(f"config {cfg} is still the untouched example — EDIT IT: "
                   "timezone, trusted_chat_ids, agent_names")
        else:
            r.ok(f"config {cfg} is valid JSON")
    elif not check_only:
        shutil.copyfile(EXAMPLE, cfg)
        r.warn(f"seeded {cfg} from the example — EDIT IT: timezone, trusted_chat_ids, agent_names")
    else:
        r.warn(f"no {cfg} — defaults apply: UTC, and NO group chat corroborates memory (fail-closed)")


def check_dry_run(dream, cfg, r):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dream.main(["--dry-run", "--config", str(cfg)])
    except Exception as exc:  # noqa: BLE001 — report, do not crash the installer
        r.bad(f"dry run failed: {type(exc).__name__}: {exc}")
        return
    if rc:
        r.bad("dry run failed: " + " | ".join(err.getvalue().strip().splitlines()[-3:]))
        return
    source_line = next((ln for ln in out.getvalue().splitlines() if ln.startswith("fact source:")), "")
    r.ok(f"dry run: {source_line or 'ok'}")


def _memory_provider(home, dream):
    """`memory.provider` of Hermes config.yaml, read as the pass reads it (`dream.hermes_memory_settings`:
    YAML when PyYAML is there, else a scanner that skips comments); None when the file cannot be read.
    The installer had its own block regex, which stopped at a top-level comment inside `memory:` and
    reported no provider while Hermes ran holographic (issue #2)."""
    try:
        (home / "config.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    provider = dream.hermes_memory_settings(str(home)).get("provider")
    return str(provider).strip() if provider is not None else ""


def _write_approval(home, dream):
    """`memory.write_approval` of Hermes config.yaml, coerced as Hermes does (`dream.hermes_enabled`); False when
    unreadable."""
    return dream.hermes_memory_settings(str(home)).get("write_approval") is True


def _check_holographic_version(home, r):
    """The catalog plugin 0.2.0–0.7.x refuses every fact_store write in a cron session: the extraction job
    and the keyword pass would answer "read-only" and store nothing, silently. 0.8.0 accepts a cron job's
    explicit calls. Read from the installed plugin's manifest; nothing said when there is none."""
    manifest = home / "plugins" / "holographic" / "plugin.yaml"
    try:
        found = re.search(r"(?m)^version:\s*['\"]?(\d+)\.(\d+)", manifest.read_text(encoding="utf-8"))
    except OSError:
        return
    if not found:
        return
    version = (int(found.group(1)), int(found.group(2)))
    if (0, 2) <= version < (0, 8):
        r.warn(f"holographic {found.group(1)}.{found.group(2)} refuses fact_store writes from cron jobs — the "
               "extraction job and keywords_needed store nothing; update it: hermes plugins update holographic "
               "(0.8.0 or later)")
    elif version >= (0, 8):
        r.ok(f"holographic {found.group(1)}.{found.group(2)}: cron jobs may write facts; forgotten facts are swept")


def _check_holographic_origins(path, r):
    """Whether the store itself keeps who stands behind a fact and confirmations (holographic >= 0.15.0). The file
    is what counts, not the plugin's version: the columns appear on a writer's open, so a store an updated plugin
    has not opened yet has none (Codex review of the 2.9.0 plan). Read-only."""
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            origin = any(row[1] == "src_origin" for row in conn.execute("PRAGMA table_info(fact_versions)"))
            confirmations = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fact_confirmations'").fetchone())
        finally:
            conn.close()
    except sqlite3.Error:
        return
    if origin and confirmations:
        r.ok("the fact store keeps origins and confirmations — items carry `origin`, the confirm section is on")
    else:
        r.ok("the fact store keeps no origins or confirmations yet (holographic older than 0.15.0, or not opened "
             "by it since the update) — no `origin` on items, the confirm section stays silent")


def _check_holographic_settings(home, dream, r):
    """Two provider settings the dream has to say something about (holographic >= 0.14).

    Neither breaks the pass; both change what a reader of the nightly report should expect,
    and `mirror_memory_writes` makes the pass feed itself."""
    embeddings = str(dream._hermes_plugin_setting("embeddings", home) or "").strip().lower()
    if embeddings in ("on", "true", "yes", "1"):
        r.ok("holographic search by meaning is on — the pass itself still reads words only "
             "(vectors are the provider's business); keywords stay the offline path when the "
             "embeddings endpoint is unreachable")
    mirror = str(dream._hermes_plugin_setting("mirror_memory_writes", home) or "").strip().lower()
    if mirror in ("true", "yes", "1", "on"):
        r.warn("holographic mirror_memory_writes is true — every memory entry becomes a fact, so "
               "the pass's own promotions come back as new facts (and prefetch injects what the "
               "system prompt already holds in full). The provider's default is false")
    floor = dream.holographic_min_trust(home)
    if dream.FEEDBACK_FLOOR_EXPLICIT:
        if floor is not None and abs(floor - dream.FEEDBACK_TRUST_FLOOR) > 1e-9:
            r.ok(f"holographic min_trust_threshold is {floor:g}, but gates.feedback_trust_floor "
                 f"{dream.FEEDBACK_TRUST_FLOOR:g} in dreaming.json overrides it for trust_feedback")
    elif floor is not None and abs(floor - 0.3) > 1e-9:
        r.ok(f"holographic min_trust_threshold is {floor:g} — trust_feedback uses that floor, "
             "not the 0.3 default")


def check_fact_source(home, dream, r):
    try:
        source = dream.fact_source()
    except ValueError as exc:
        r.bad(str(exc))
        return
    if source == "none":
        r.ok("fact_source 'none' — the dream reviews the memory files only (no candidates to promote)")
        return
    path = dream.fact_store_path()
    if source != "holographic":
        if not path:
            r.bad(f"fact_source '{source}' needs fact_store_path in dreaming.json")
        elif not os.path.exists(path):
            r.warn(f"fact_source '{source}': {path} does not exist yet — the pass treats it as empty")
        else:
            r.ok(f"fact_source '{source}', store present ({path}); extraction stays off "
                 "(it feeds the holographic store only)")
        return
    provider = _memory_provider(home, dream)
    if provider is None:
        r.warn(f"cannot read {home / 'config.yaml'} — check that memory.provider is set")
    elif provider != "holographic":
        # Hermes ships the holographic provider but leaves memory.provider empty.
        # Without it there is no fact store: the pass still reviews the memory
        # files, but promotions/new_facts/conflicts stay empty forever.
        advice = ("Set 'memory.provider: holographic', point fact_source at your own export, "
                  "or set fact_source to 'none'. NOTE: holographic left the Hermes core on "
                  "2026-10-11 (v0.21.7) — install it as a plugin: hermes plugins install holographic "
                  "(itpartypattaya/hermes-plugin-holographic); if `hermes update` turned memory off, "
                  "see that README. 'none' needs nothing and keeps the nightly memory review working")
        if _write_approval(home, dream):
            # Worth saying before the advice is taken: `fact_store` writes to
            # memory_store.db directly, and the approval gate covers MEMORY.md,
            # USER.md and skills — not the provider (external review, 2026-10-07).
            advice = ("⚠️ memory.write_approval is ON, and the holographic provider writes facts "
                      "through `fact_store` — a channel the approval gate does NOT cover (it gates "
                      "MEMORY.md, USER.md and skills), including from cron; its prefetch then feeds "
                      "those facts into ordinary turns. If that is not what you want, set "
                      "fact_source to 'none' and keep the nightly memory review only")
        r.warn(f"memory.provider is {provider or 'not set'} — there is no holographic fact store, so "
               f"promotions stay empty. {advice}")
    elif os.path.exists(path):
        r.ok(f"memory provider 'holographic', fact store present ({path})")
        _check_holographic_version(home, r)
        _check_holographic_origins(path, r)
    else:
        r.warn(f"memory provider 'holographic', but no {path} yet — the pass treats it as empty; "
               "the file appears with the first stored fact")
    if provider == "holographic":
        note = dream.fact_store_note(home)
        if note:
            r.warn(note)
        _check_holographic_settings(home, dream, r)


def _load_install_cron(home):
    spec = importlib.util.spec_from_file_location("_install_cron_for_check",
                                                  SKILL_DIR / "scripts" / "install_cron.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HOME = Path(home)
    return mod


def check_jobs(home, check_only, r):
    # A job keeps the skill name it was created with. If that skill stops
    # loading — a 2.0 job still bound to a regular `dreaming`, a plugin disabled
    # or removed after install_cron — Hermes runs the job without it and only
    # flags the skipped skill at the top of the nightly report.
    path = home / "cron" / "jobs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        r.warn(f"cannot read {path}: {exc}")
        return
    listed = data.get("jobs", []) if isinstance(data, dict) else data
    ours = [j for j in listed if isinstance(j, dict) and j.get("script") in SKILL_GATES]
    if not ours:
        if check_only:
            r.warn("no dreaming cron jobs yet — create them with install_cron.py")
        return
    ic = _load_install_cron(home)
    # Jobs made before 2.4.0 have no forget sweep: a fact the human asked to forget then waits for the night
    # in every copy the dream keeps, and nothing says so (review 2026-10-13).
    if not any(isinstance(j, dict) and j.get("script") == ic.SWEEP_SCRIPT for j in listed):
        r.warn(f"no {ic.SWEEP_SCRIPT} cron job — forgotten facts are cleaned from the dream's copies only "
               "at night; run install_cron.py (it keeps the existing jobs), or --sweep-schedule '' on purpose")
    try:
        expected, problem = ic.resolve_skill()
    except ic.RegistryError as exc:
        r.warn(f"cannot tell which skill the cron jobs should load: {exc}")
        return
    if problem:
        r.bad(f"the dreaming cron jobs cannot load their skill: {problem}")
        return
    for job in ours:
        label = f"job {job.get('id')} ({job.get('script')})"
        if ic.bound_to(job, expected):
            r.ok(f"{label} loads {expected}")
        else:
            current = ic.job_skills(job)
            r.bad(f"{label} loads {', '.join(current) or 'no skill'}, but this install provides "
                  f"{expected} — run install_cron.py --rebind")


def check_core(home, r):
    # Hermes >= 0.21 gives cron sessions memory like any other session, so the
    # extraction job can call the provider's fact_store. Older cores keep the
    # provider out of cron: the dream still works, extraction cannot (read-only
    # check; nothing here touches the core — see references/cron-memory.md).
    sched = home / "hermes-agent" / "cron" / "scheduler.py"
    try:
        text = sched.read_text(encoding="utf-8")
    except OSError:
        r.warn(f"no {sched} — cannot tell whether cron sessions may touch memory")
        return
    if re.search(r"^ *skip_memory=False,?$", text, re.MULTILINE):
        r.ok("core gives cron jobs memory natively (Hermes >= 0.21) — the extraction job can use fact_store")
    else:
        r.warn("this Hermes keeps memory providers out of cron sessions: the nightly dream works, "
               "but the extraction job cannot call fact_store — upgrade to Hermes >= 0.21, or "
               "install the jobs with --extract-schedule \"\"")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install / verify the dreaming cron gates")
    ap.add_argument("--check", action="store_true", help="verify only, change nothing")
    ap.add_argument("--hermes-home", default=None,
                    help="Hermes home (default: $HERMES_HOME or ~/.hermes)")
    args = ap.parse_args(argv)
    home = Path(args.hermes_home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    cfg = Path(os.environ.get("DREAM_CONFIG") or home / "dreaming.json")

    print("== hermes-dreaming installer ==")
    print(f"   skill:       {SKILL_DIR}")
    print(f"   HERMES_HOME: {home}")
    if not home.is_dir():
        print(f"  ✗ {home} does not exist — pass --hermes-home or set HERMES_HOME")
        return 1

    r = Report()
    check_gates(home, args.check, r)
    check_config(cfg, args.check, r)
    dream = _load_dream(home, cfg)
    check_dry_run(dream, cfg, r)
    check_core(home, r)
    check_fact_source(home, dream, r)
    check_jobs(home, args.check, r)

    print(f"\n== {r.ok_n} ok, {r.warn_n} warning(s), {r.bad_n} problem(s) ==")
    if not r.bad_n and not args.check:
        print("\nNext — create the cron jobs (the CLI cannot set enabled_toolsets, so use the "
              "installer):\n\n"
              f"  {home / 'hermes-agent' / 'venv' / 'bin' / 'python'} \\\n"
              f"      {SKILL_DIR / 'scripts' / 'install_cron.py'} --deliver local --dry-run\n"
              "  # happy with the plan? drop --dry-run")
    return 1 if r.bad_n else 0


if __name__ == "__main__":
    raise SystemExit(main())
