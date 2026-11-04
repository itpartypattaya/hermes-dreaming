#!/usr/bin/env python3
"""Contract with a Hermes core release: the files core writes and the dream reads, made by core itself.

The unit tests feed the dream hand-written fixtures of core's files. A core release that renames a field of
`cron/jobs.json`, moves a key of `config.yaml`, changes the §-format of the memory files or a column of
`state.db` passes them all, and the night then degrades in silence (no jobs found, no limits, no messages).
Here a clean HERMES_HOME is filled by core's own code — its CLI, its cron API, its memory tool, its session
store — the plugin is installed the way a user installs it, the installers run as in production, and both
sides are asked what they see. Every check is an assert naming what diverged.

Not covered: `memory_store.db` belongs to the holographic plugin, not to core (provider-integration covers it).

Needs the core installed in this interpreter (the `core-contract` job in .github/workflows/tests.yml) and:
    HERMES_HOME        an empty or absent directory, filled from scratch
    HERMES_AGENT_REPO  the core checkout (linked as $HERMES_HOME/hermes-agent, the production layout)
    PLUGIN_REPO        this repository as a Git checkout (installed with `hermes plugins install file://…`)
"""

from __future__ import annotations

import filecmp
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ["HERMES_HOME"]).resolve()
CORE = Path(os.environ["HERMES_AGENT_REPO"]).resolve()
PLUGIN = Path(os.environ.get("PLUGIN_REPO") or Path(__file__).resolve().parents[2]).resolve()
INSTALLED = HOME / "plugins" / "hermes-dreaming" / "skills" / "dreaming"   # the copy the jobs run, as in production
SCRIPTS = INSTALLED / "scripts"

MEMORY_LIMIT, USER_LIMIT = 3100, 1700          # not core's defaults: the dream must take them from config.yaml
TIMEZONE = "Asia/Tokyo"
CHAT = "-1009990001"                           # the trusted group of this home
SAID = "Our family moves to Vila Real in spring 2027, the flat search starts in January"
MEMORY_ENTRIES = [
    "Rule: a bare § inside a line is text, not an entry delimiter",
    "Backups run every night at 04:00\nand are kept for seven days",
]
USER_ENTRIES = ["Leo prefers short answers with the result first"]
SCHEDULES = {"dream-precheck.py": "0 3 * * *", "dream-extract-precheck.py": "30 2 * * 1",
             "dream-forget-sweep.py": "*/15 * * * *"}
AGENT_SCRIPTS = ("dream-precheck.py", "dream-extract-precheck.py")


def ok(message):
    print(f"ok: {message}", flush=True)


def run(argv, what):
    """A command as a user (or the scheduler) runs it; its output goes to the log either way."""
    print(f"\n$ {' '.join(str(a) for a in argv)}", flush=True)
    proc = subprocess.run([str(a) for a in argv], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=900)
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr, flush=True)
    assert proc.returncode == 0, f"{what}: exit {proc.returncode}"
    return proc.stdout


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fresh_home():
    if HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True)
    (HOME / "hermes-agent").symlink_to(CORE, target_is_directory=True)


def core_config():
    """config.yaml through `hermes config set`, the way a user writes it; then core reads it back."""
    for key, value in (("memory.memory_char_limit", MEMORY_LIMIT), ("memory.user_char_limit", USER_LIMIT),
                       ("memory.provider", "holographic"), ("timezone", TIMEZONE)):
        run(["hermes", "config", "set", key, value], f"hermes config set {key}")
    from hermes_cli.config import load_config
    mem = load_config().get("memory") or {}
    assert (mem.get("memory_char_limit"), mem.get("user_char_limit")) == (MEMORY_LIMIT, USER_LIMIT), (
        f"core does not read back the limits it wrote: {mem}")
    ok("config.yaml written by `hermes config set`, read back by core")


def core_plugin():
    """The plugin installed and enabled by core's own installer; the qualified skill name from its registry."""
    # --force: a file:// source is "community", and the install scanner blocks a community source with a caution
    # verdict (the screen's injection patterns, the tests' fixtures) unless told to; the catalog entry users
    # install is reviewed and not blocked. The scan itself is the validate job's business.
    run(["hermes", "plugins", "install", f"file://{PLUGIN}", "--enable", "--yes-deps", "--force"],
        "hermes plugins install")
    assert (HOME / "plugins" / "hermes-dreaming" / "plugin.json").is_file(), (
        f"`hermes plugins install` did not put the plugin at {HOME / 'plugins' / 'hermes-dreaming'}, where "
        "install_cron.py, install.py and the gates look for it")
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    discover_plugins(force=True)
    pm = get_plugin_manager()
    own = (INSTALLED / "SKILL.md").resolve()
    names = [m["name"] for m in pm.list_plugin_skill_metadata()
             if str(m.get("name", "")).endswith(":dreaming") and pm.find_plugin_skill(m["name"])
             and Path(pm.find_plugin_skill(m["name"])).resolve() == own]
    assert len(names) == 1, (f"core's plugin registry should register exactly one skill ending in ':dreaming' "
                             f"at {own}, found {names} among {[m['name'] for m in pm.list_plugin_skill_metadata()]}")
    ok(f"plugin installed and enabled by core, skill registered as {names[0]}")
    return pm, names[0]


def core_memory():
    """MEMORY.md / USER.md through core's memory tool (no model involved), as the agent writes them."""
    from tools.memory_tool import load_on_disk_store, memory_tool
    store = load_on_disk_store()
    assert (store.memory_char_limit, store.user_char_limit) == (MEMORY_LIMIT, USER_LIMIT), (
        f"core's memory store ignores config.yaml: {store.memory_char_limit}/{store.user_char_limit}")
    result = json.loads(memory_tool(target="memory", store=store,
                                    operations=[{"action": "add", "content": e} for e in MEMORY_ENTRIES]))
    assert result.get("success"), f"core's memory tool refused the MEMORY.md batch: {result}"
    result = json.loads(memory_tool(action="add", target="user", content=USER_ENTRIES[0], store=store))
    assert result.get("success"), f"core's memory tool refused the USER.md add: {result}"
    store = load_on_disk_store()
    assert (store.memory_entries, store.user_entries) == (MEMORY_ENTRIES, USER_ENTRIES), (
        f"core does not read back its own entries: {store.memory_entries} / {store.user_entries}")
    for name in ("MEMORY.md", "USER.md"):
        assert (HOME / "memories" / name).is_file(), f"core's memory tool wrote no {HOME / 'memories' / name}"
    ok("memories/MEMORY.md and USER.md written by core's memory tool")
    return store


def core_sessions():
    """state.db through core's session store: a human in the trusted group, and a cron session's own prompt."""
    from hermes_state import SessionDB
    db = SessionDB()
    try:
        db.create_session("contract-group", "telegram", chat_id=CHAT, chat_type="group", user_id="4242")
        db.append_message("contract-group", "user", content=SAID)
        db.append_message("contract-group", "assistant", content="Noted, I will remember the move.")
        db.create_session("contract-cron", "cron")
        db.append_message("contract-cron", "user", content="Process the nightly dream JSON handed over by the gate.")
    finally:
        db.close()
    assert (HOME / "state.db").is_file(), f"core's session store wrote no {HOME / 'state.db'}"
    ok("state.db written by core's session store")


def state_columns():
    """Every column the dream's queries name exists in the state.db core created. A missing one is not an error
    there: the dream falls back (no sessions table → no chat metadata), so only this check notices."""
    import sqlite3
    conn = sqlite3.connect(f"file:{HOME / 'state.db'}?mode=ro", uri=True)
    try:
        cols = {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in ("messages", "sessions")}
    finally:
        conn.close()
    wanted = {"messages": {"id", "session_id", "role", "content", "timestamp", "observed", "active", "compacted",
                           "display_kind", "tool_calls"},
              "sessions": {"id", "source", "chat_type", "chat_id", "thread_id", "user_id"}}
    for table, names in wanted.items():
        assert names <= cols[table], (f"state.db table {table} written by core lacks the columns the dream reads: "
                                      f"{sorted(names - cols[table])} (core has {sorted(cols[table])})")
    ok("state.db has every column the dream reads")


def edit_dreaming_json():
    """Step 3 of the README, done the way the test needs it: the trusted chat, and the limits and timezone left
    to Hermes (an explicit value in dreaming.json would hide whether the dream reads config.yaml)."""
    path = HOME / "dreaming.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.update(trusted_chat_ids=[CHAT], excluded_threads={})
    for key in ("timezone", "memory_char_limits"):
        cfg.pop(key, None)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def core_jobs(cj, pm, skill, ic):
    """What core's cron API reads from the jobs.json after install_cron.py: one runnable job per gate, with the
    fields the scheduler acts on."""
    raw = json.loads((HOME / "cron" / "jobs.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict) and isinstance(raw.get("jobs"), list), (
        f"cron/jobs.json is no longer {{'jobs': [...]}} (top-level keys {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}): "
        "install.py --check, install_cron.py --dry-run and the forget sweep read it in that shape")
    from cron.scheduler_script import _resolve_script_path
    with cj.use_cron_store(HOME):
        listed = cj.list_jobs(include_disabled=True)
    found = {}
    for script, expr in SCHEDULES.items():
        group = [j for j in listed if j.get("script") == script]
        assert len(group) == 1, f"core lists {len(group)} jobs with script={script!r}, expected 1: {group}"
        job = found[script] = group[0]
        label = f"job {job.get('id')} ({script})"
        assert job.get("enabled") is True and cj.is_job_runnable(job), f"{label}: core would not run it: {job}"
        assert job.get("state") == "scheduled", f"{label}: state {job.get('state')!r}, expected 'scheduled'"
        schedule = job.get("schedule") or {}
        assert (schedule.get("kind"), schedule.get("expr")) == ("cron", expr), (
            f"{label}: core reads the schedule as {schedule}, expected cron {expr!r}")
        assert job.get("next_run_at"), f"{label}: core computed no next_run_at"
        assert job.get("deliver") == "local", f"{label}: deliver {job.get('deliver')!r}, expected 'local'"
        path, error = _resolve_script_path(job["script"])
        assert path is not None, f"{label}: core cannot resolve the gate: {error}"
        assert path == (HOME / "scripts" / script).resolve(), f"{label}: core runs {path}, not the installed gate"
        assert filecmp.cmp(path, SCRIPTS / script, shallow=False), f"{label}: the gate core runs differs from the skill's"
        if script in AGENT_SCRIPTS:
            prompt = ic.DREAM_PROMPT if script == "dream-precheck.py" else ic.EXTRACT_PROMPT
            assert job.get("no_agent") is False, f"{label}: no_agent {job.get('no_agent')!r}, the job needs the agent"
            assert job.get("skills") == [skill] and job.get("skill") == skill, (
                f"{label}: core reads skills={job.get('skills')!r} skill={job.get('skill')!r}, expected [{skill!r}]")
            skill_path = pm.find_plugin_skill(skill)
            assert skill_path and Path(skill_path).resolve() == (INSTALLED / "SKILL.md").resolve(), (
                f"{label}: core resolves its skill {skill!r} to {skill_path}, not the installed plugin's SKILL.md")
            assert job.get("enabled_toolsets") == ["memory"], (
                f"{label}: enabled_toolsets {job.get('enabled_toolsets')!r}, expected ['memory']")
            assert job.get("prompt") == prompt, f"{label}: core holds another prompt than install_cron.py wrote"
        else:
            assert job.get("no_agent") is True, f"{label}: no_agent {job.get('no_agent')!r}, the sweep runs without a model"
    assert len(listed) == len(SCHEDULES), f"core lists {len(listed)} jobs, expected {len(SCHEDULES)}: {listed}"
    ok("core lists the three dreaming jobs with schedule, gate, skill, toolsets, prompt and mode as installed")
    return found


def dream_side(store, jobs, cj):
    """What dream.py itself reads from the same files."""
    dream = load("_dream_contract", SCRIPTS / "dream.py")
    dream.load_for_home(str(HOME))
    home = str(HOME)

    with cj.use_cron_store(HOME):
        out_dir = cj.get_cron_output_dir()
    errors = []
    dirs = dream._dream_job_dirs(home, errors)
    assert not errors, f"dream.py cannot read the cron/jobs.json core wrote: {errors}"
    want = sorted(str(out_dir / jobs[s]["id"]) for s in AGENT_SCRIPTS)
    assert sorted(dirs) == want, (f"dream.py looks for the dream jobs' logs in {sorted(dirs)}, core writes them "
                                  f"to {want}")
    ok("dream.py finds both dream jobs in cron/jobs.json and their output folders where core writes them")

    for target, name, entries in (("memory", "MEMORY.md", MEMORY_ENTRIES), ("user", "USER.md", USER_ENTRIES)):
        text = (HOME / "memories" / name).read_text(encoding="utf-8")
        assert dream._memory_entries(text) == entries, (
            f"dream.py splits {name} into {dream._memory_entries(text)}, core wrote {entries}")
        assert len(text) == store._char_count(target), (
            f"{name}: the dream counts {len(text)} chars, core {store._char_count(target)} — memory_pressure would lie")
    ok("dream.py reads the §-entries and the char counts of MEMORY.md / USER.md exactly as core does")

    assert dream.hermes_config_unread(home) == (), (
        f"dream.py cannot read these blocks of core's config.yaml for sure: {dream.hermes_config_unread(home)}")
    settings = dream.hermes_memory_settings(home)
    assert (settings.get("memory_char_limit"), settings.get("user_char_limit"), settings.get("provider")) == (
        MEMORY_LIMIT, USER_LIMIT, "holographic"), f"dream.py reads memory: of core's config.yaml as {settings}"
    assert dream.SETTINGS.MEMORY_CHAR_LIMITS == {"memories/MEMORY.md": MEMORY_LIMIT, "memories/USER.md": USER_LIMIT}, (
        f"the pass took the char limits {dream.SETTINGS.MEMORY_CHAR_LIMITS}, config.yaml says {MEMORY_LIMIT}/{USER_LIMIT}")
    assert str(dream.SETTINGS.LOCAL_TZ) == TIMEZONE, f"the pass runs on {dream.SETTINGS.LOCAL_TZ}, config.yaml says {TIMEZONE}"
    # Core reads its YAML with ruamel and does not install PyYAML (0.22.6): in core's own environment the dream
    # reads config.yaml with its stdlib scanner, which must see what core sees.
    from hermes_cli.config import load_config
    core_cfg = load_config()
    text = (HOME / "config.yaml").read_text(encoding="utf-8")
    scanned = dream._scan_block(text, ("memory",))
    assert scanned is not None, "the dream's stdlib reader cannot read memory: of core's config.yaml at all"
    for key in ("memory_char_limit", "user_char_limit", "provider"):
        assert scanned.get(key) == core_cfg["memory"].get(key), (
            f"memory.{key}: the dream's stdlib reader gives {scanned.get(key)!r}, core {core_cfg['memory'].get(key)!r}")
    assert (dream._scan_block(text, ()) or {}).get("timezone") == core_cfg.get("timezone") == TIMEZONE, (
        "timezone: the dream's stdlib reader and core disagree on core's config.yaml")
    ok("dream.py reads memory limits, provider and timezone from core's config.yaml as core reads them")

    messages = dream.load_messages(str(HOME / "state.db"), 30)
    assert messages is not None, "dream.py found core's state.db busy or unreadable"
    assert [m["content"] for m in messages] == [SAID], (
        f"dream.py reads {[m['content'] for m in messages]} from core's state.db, expected only the human in the "
        "trusted group (the cron session's prompt and the assistant's answer are not speech)")
    m = messages[0]
    assert (m["source"], m["chat_id"], m["user_id"], m["session_found"]) == ("telegram", CHAT, "4242", True), (
        f"dream.py reads the message's origin as {m}")
    ok("dream.py reads the human message, its chat and sender from core's state.db, and skips the cron prompt")


def gates_through_core(jobs, store):
    """Each gate run by core's own script runner, its stdout read by core's wake-gate parser."""
    from cron.scheduler_prompt import _parse_wake_gate
    from cron.scheduler_script import _run_job_script
    for script, job in jobs.items():
        success, output = _run_job_script(job["script"], workdir=job.get("workdir"), interpreter=job.get("interpreter"))
        print(f"\n[{script} through core]\n{output}", flush=True)
        assert success, f"core's script runner failed {script}: {output}"
        if script == "dream-forget-sweep.py":
            assert output == "", f"{script}: nothing to clean must print nothing (core delivers any stdout): {output!r}"
            continue
        last = [line for line in output.splitlines() if line.strip()][-1]
        gate = json.loads(last)
        assert "dream_error" not in gate, f"{script} failed inside: {gate['dream_error']}"
        assert _parse_wake_gate(output) is (gate != {"wakeAgent": False}), (
            f"{script}: core reads the gate's answer {last[:200]!r} as wake={_parse_wake_gate(output)}")
    ok("core runs the three gates and reads their wake answers as the gates mean them")

    data = json.loads((HOME / "cache" / "dream.json").read_text(encoding="utf-8"))
    usage = data.get("memory_usage") or {}
    for target, name, limit in (("memory", "memories/MEMORY.md", MEMORY_LIMIT), ("user", "memories/USER.md", USER_LIMIT)):
        rec = usage.get(name) or {}
        assert (rec.get("chars"), rec.get("limit")) == (store._char_count(target), limit), (
            f"the nightly pass reports {name} as {rec}, core holds {store._char_count(target)}/{limit} chars")
    ok("the nightly pass, run by core, reports the memory fill level core itself counts")


def memory_edits(ic):
    """The memory actions the nightly prompt asks for are the ones core's memory tool offers, and a `patch` built from
    what the pass hands over (`nearest_entry.full_entry`) changes the entry the way the prompt says — live, and as a
    write core stages for approval, which the pass must then count as waiting."""
    import re
    from tools.memory_tool import MEMORY_SCHEMA, load_on_disk_store, memory_tool
    try:
        offered = set(MEMORY_SCHEMA["parameters"]["properties"]["operations"]["items"]["properties"]["action"]["enum"])
    except (KeyError, TypeError) as exc:
        raise AssertionError(f"core's memory tool schema no longer lists operations[].action ({exc!r})") from exc
    named = set(re.findall(r"\bmemory (add|patch|remove|replace|update|delete)\b", ic.DREAM_PROMPT))
    assert named and named <= offered, (
        f"the nightly prompt asks for memory {sorted(named - offered)}, core's memory tool offers {sorted(offered)}: "
        "every such write is refused at night")
    assert "patch" in offered, f"core's memory tool has no patch ({sorted(offered)}): the prompt's way to change an entry"
    ok(f"the nightly prompt asks only for memory actions core offers ({sorted(named)} of {sorted(offered)})")

    dream = sys.modules["_dream_contract"]
    mem_md = str(HOME / "memories" / "MEMORY.md")
    entry = "Masha (daughter, 7): ballet on Mondays at 17:30\nNina picks her up afterwards and Leo pays the studio"
    store = load_on_disk_store()
    assert json.loads(memory_tool(action="add", target="memory", content=entry, store=store)).get("success")

    def near():
        found = dream.nearest_entry("Masha has ballet on Mondays and Thursdays at 17:30, Nina picks her up",
                                    sources=dream.load_durable_memory_sources(mem_md))
        assert found and found.get("full_entry") == entry_now[0] and found.get("target") == "memory", (
            f"nearest_entry does not hand over the entry core holds: {found}")
        return found

    def entries():
        return dream._memory_entries((HOME / "memories" / "MEMORY.md").read_text(encoding="utf-8"))

    entry_now = [entry]
    # a span copied verbatim from full_entry, its new text: the rest of the entry stays
    span = "Mondays at 17:30"
    assert span in near()["full_entry"]
    result = json.loads(memory_tool(target="memory", store=store, operations=[
        {"action": "patch", "old_text": span, "content": "Mondays and Thursdays at 17:30"}]))
    assert result.get("success") and not result.get("staged"), f"core refused the span patch: {result}"
    entry_now[0] = entry.replace(span, "Mondays and Thursdays at 17:30")
    assert entry_now[0] in entries() and entry not in entries(), f"the span patch left {entries()}"
    # the whole full_entry as old_text rewrites the entry
    rewritten = "Masha (daughter, 7): ballet on Mondays and Thursdays at 17:30, Nina picks her up, Leo pays"
    result = json.loads(memory_tool(target="memory", store=store, operations=[
        {"action": "patch", "old_text": near()["full_entry"], "content": rewritten}]))
    assert result.get("success"), f"core refused the whole-entry patch: {result}"
    assert rewritten in entries() and not any(e != rewritten and "Masha" in e for e in entries()), (
        f"patching the whole full_entry did not rewrite the entry: {entries()}")
    entry_now[0] = rewritten
    ok("a patch from nearest_entry.full_entry changes one span, or the whole entry, as the prompt says")

    run(["hermes", "config", "set", "memory.write_approval", "true"], "hermes config set memory.write_approval")
    store = load_on_disk_store()
    result = json.loads(memory_tool(target="memory", store=store, operations=[
        {"action": "patch", "old_text": "Leo pays", "content": "Leo pays the studio on the 5th"}]))
    assert result.get("staged"), f"with write_approval on, core did not stage the patch: {result}"
    assert rewritten in entries(), "a staged patch changed MEMORY.md"
    pending = dream.load_pending_writes(str(HOME))
    assert [p["action"] for p in pending] == ["patch"], f"the pass reads core's approval queue as {pending}"
    want = rewritten.replace("Leo pays", "Leo pays the studio on the 5th")
    assert want in dream._staged_text(pending), (
        f"the pass does not count the staged patch as waiting: {dream._staged_text(pending)!r}, entry after it {want!r}")
    assert dream._staged_removals(pending) == [rewritten], (
        f"the pass does not see which entry the staged patch changes: {dream._staged_removals(pending)}")
    run(["hermes", "config", "set", "memory.write_approval", "false"], "hermes config set memory.write_approval")
    ok("a patch core staged for approval counts as waiting in the pass, and its entry as going away")


def main():
    fresh_home()
    core_config()
    pm, skill = core_plugin()
    store = core_memory()
    core_sessions()
    state_columns()

    py = sys.executable
    out = run([py, SCRIPTS / "install.py", "--hermes-home", HOME], "install.py")
    assert "core gives cron jobs memory natively" in out, (
        "install.py no longer recognises core's cron scheduler (check_core reads skip_memory=False from "
        "cron/scheduler.py) — the user is told cron has no memory")
    for script in SCHEDULES:
        assert filecmp.cmp(HOME / "scripts" / script, SCRIPTS / script, shallow=False), f"install.py did not copy {script}"
    ok("install.py copied the gates and recognised the core")
    edit_dreaming_json()

    plan = json.loads(run([py, SCRIPTS / "install_cron.py", "--hermes-home", HOME, "--deliver", "local", "--dry-run"],
                          "install_cron.py --dry-run"))
    creates = {p["script"]: p for p in plan if p.get("action") == "create"}
    assert sorted(creates) == sorted(SCHEDULES), f"the dry run plans to create {sorted(creates)}, expected all three gates"
    assert all(creates[s]["skill"] == skill for s in AGENT_SCRIPTS), (
        f"the dry run binds {[creates[s]['skill'] for s in AGENT_SCRIPTS]}, core registered {skill}")
    ok("install_cron.py --dry-run plans the three jobs with core's skill name")

    out = run([py, SCRIPTS / "install_cron.py", "--hermes-home", HOME, "--deliver", "local"], "install_cron.py")
    assert out.count("OK: created") == 3, "install_cron.py did not report three created jobs"
    from cron import jobs as cj
    ic = load("_install_cron_contract", SCRIPTS / "install_cron.py")
    jobs = core_jobs(cj, pm, skill, ic)
    ids = sorted(j["id"] for j in jobs.values())

    # Re-runs as after an update: nothing new, every check passes against what core holds.
    for flags, what in (([], "install_cron.py (again)"), (["--upgrade"], "install_cron.py --upgrade")):
        out = run([py, SCRIPTS / "install_cron.py", "--hermes-home", HOME, "--deliver", "local", *flags], what)
        assert "created" not in out, f"{what} created jobs again — it does not recognise the jobs core holds"
        assert "PROBLEM" not in out and "ERROR" not in out, f"{what} reports problems on a fresh install"
    with cj.use_cron_store(HOME):
        again = sorted(j["id"] for j in cj.list_jobs(include_disabled=True))
    assert again == ids, f"after the re-runs core lists {again}, before {ids}"
    out = run([py, SCRIPTS / "install.py", "--check", "--hermes-home", HOME], "install.py --check")
    assert out.count(f"loads {skill}; prompt current") == 2, (
        f"install.py --check does not confirm both agent jobs load {skill} with the current prompt")
    assert "✗" not in out, "install.py --check reports a problem on a fresh install"
    out = run(["hermes", "cron", "list", "--all"], "hermes cron list --all")
    for job in jobs.values():
        assert job["id"] in out, f"`hermes cron list --all` does not show job {job['id']} ({job['script']})"
    ok("re-runs change nothing; install.py --check and `hermes cron list` see the jobs")

    dream_side(store, jobs, cj)
    gates_through_core(jobs, store)
    memory_edits(ic)
    print("\ncore contract: all checks passed", flush=True)


if __name__ == "__main__":
    main()
