#!/usr/bin/env bash
# install.sh — put the cron gates where Hermes can run them, and verify the install.
#
# Hermes only executes cron scripts that physically live in $HERMES_HOME/scripts
# (the scheduler resolves the path and refuses anything outside that directory —
# a symlink does not help). So the two pre-check scripts must be COPIED there,
# which means they can silently drift from the skill after a `git pull`.
# `--check` is the guard against exactly that.
#
#   ./install.sh            copy the gates, seed a config if absent, then verify
#   ./install.sh --check    verify only, change nothing (exit 1 on any problem)
#
# Everything is idempotent: re-running is safe.
set -uo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATES=(dream-precheck.py dream-extract-precheck.py)
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

PASS=0; FAIL=0; WARN=0
ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ✗ $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ! $1"; WARN=$((WARN+1)); }

echo "== hermes-dreaming installer =="
echo "   skill:       $SKILL_DIR"
echo "   HERMES_HOME: $HERMES_HOME"

if [ ! -d "$HERMES_HOME" ]; then
  echo "  ✗ $HERMES_HOME does not exist — set HERMES_HOME to your Hermes home"
  exit 1
fi

# ── 1) the cron gates ─────────────────────────────────────────────────────────
if [ "$CHECK_ONLY" -eq 0 ]; then
  mkdir -p "$HERMES_HOME/scripts"
  for g in "${GATES[@]}"; do
    cp "$SKILL_DIR/scripts/$g" "$HERMES_HOME/scripts/$g" && chmod 0755 "$HERMES_HOME/scripts/$g"
  done
fi
for g in "${GATES[@]}"; do
  src="$SKILL_DIR/scripts/$g"; dst="$HERMES_HOME/scripts/$g"
  if [ ! -f "$dst" ]; then
    bad "$g is not in $HERMES_HOME/scripts — cron cannot run it (run ./install.sh)"
  elif ! cmp -s "$src" "$dst"; then
    bad "$g in $HERMES_HOME/scripts differs from the skill copy — re-run ./install.sh"
  else
    ok "$g in place and identical to the skill copy"
  fi
done

# ── 2) config ─────────────────────────────────────────────────────────────────
CFG="${DREAM_CONFIG:-$HERMES_HOME/dreaming.json}"
if [ -f "$CFG" ]; then
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$CFG" 2>/dev/null; then
    bad "config $CFG is not valid JSON — the pass would fall back to defaults"
  elif cmp -s "$CFG" "$SKILL_DIR/examples/dreaming.example.json"; then
    # A seeded-but-unedited config is the most common half-install: everything
    # runs, but with someone else's timezone and a placeholder chat id.
    warn "config $CFG is still the untouched example — EDIT IT: timezone, trusted_chat_ids, agent_names"
  else
    ok "config $CFG is valid JSON"
  fi
elif [ "$CHECK_ONLY" -eq 0 ]; then
  cp "$SKILL_DIR/examples/dreaming.example.json" "$CFG"
  warn "seeded $CFG from the example — EDIT IT: timezone, trusted_chat_ids, agent_names"
else
  warn "no $CFG — defaults apply: UTC, and NO group chat corroborates memory (fail-closed)"
fi

# ── 3) does the pass actually run here? ───────────────────────────────────────
# Sandbox state files: a verification run must not touch the real cooldowns.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if OUT="$(python3 "$SKILL_DIR/scripts/dream.py" --out "$TMP/dream.json" \
            --asked-state "$TMP/a.json" --seen-state "$TMP/s.json" \
            --rejected-state "" --snapshot-state "" 2>&1)"; then
  ok "dry run: $(printf '%s' "$OUT" | tail -1)"
else
  bad "dry run failed:"; printf '%s\n' "$OUT" | tail -3
fi

# ── 4) core capability: can a cron session store candidate facts? ─────────────
# Stock Hermes runs cron with skip_memory=True, which keeps the memory PROVIDER
# (and its fact_store tool) out of the session. The dream job itself is fine —
# the built-in file store is created when a job enables the `memory` toolset —
# but extraction has nothing to call. See docs/cron-memory.md.
SCHED="$HERMES_HOME/hermes-agent/cron/scheduler.py"
if [ -f "$SCHED" ]; then
  if grep -q "skip_memory=not bool(job.get(\"allow_memory\"" "$SCHED" 2>/dev/null \
     || grep -q "allow_memory" "$SCHED" 2>/dev/null; then
    ok "core honours per-job allow_memory — the extraction job can use fact_store"
  else
    warn "core runs cron with skip_memory=True: the nightly dream works, but the extraction"
    warn "job cannot call fact_store. Fix or skip it — see docs/cron-memory.md"
  fi
else
  warn "no $SCHED — cannot tell whether cron sessions may touch memory"
fi

# ── 5) memory provider: is there a fact store to consolidate at all? ──────────
# Stock Hermes ships the holographic provider but leaves `memory.provider` empty,
# and without a provider there is no `facts` table. The pass still runs and still
# reviews MEMORY.md entries, but promotions/new_facts/conflicts stay empty
# forever — worth saying out loud rather than looking like a quiet install.
PROVIDER="$(python3 - "$HERMES_HOME/config.yaml" <<'PY' 2>/dev/null || echo "unknown"
import re, sys
try:
    text = open(sys.argv[1], encoding="utf-8").read()
except OSError:
    print("noconfig"); raise SystemExit
block = re.search(r"^memory:\n((?:[ \t]+.*\n|\n)*)", text, re.MULTILINE)
if not block:
    print("nosection"); raise SystemExit
found = re.search(r"^[ \t]+provider:[ \t]*(.*?)[ \t]*$", block.group(1), re.MULTILINE)
print((found.group(1).strip().strip('"\'') if found else "") or "empty")
PY
)"
case "$PROVIDER" in
  empty|nosection)
    warn "memory.provider is not set in $HERMES_HOME/config.yaml — there is no fact store,"
    warn "so promotions/new_facts/conflicts stay empty. The MEMORY.md decay review still works."
    warn "To get the full skill: set 'memory.provider: holographic' (it ships with Hermes)." ;;
  noconfig|unknown)
    warn "cannot read $HERMES_HOME/config.yaml — check that memory.provider is set" ;;
  *)
    if [ -f "$HERMES_HOME/memory_store.db" ]; then
      ok "memory provider '$PROVIDER', fact store present ($HERMES_HOME/memory_store.db)"
    else
      warn "memory provider '$PROVIDER' configured, but no memory_store.db yet — the pass treats"
      warn "it as empty; the file appears with the first stored fact"
    fi ;;
esac

echo
echo "== $PASS ok, $WARN warning(s), $FAIL problem(s) =="
if [ "$FAIL" -eq 0 ] && [ "$CHECK_ONLY" -eq 0 ]; then
  cat <<EOF

Next — create the cron jobs (the CLI cannot set enabled_toolsets/allow_memory,
so use the installer):

  $HERMES_HOME/hermes-agent/venv/bin/python \\
      $SKILL_DIR/scripts/install_cron.py --deliver local --dry-run
  # happy with the plan? drop --dry-run
EOF
fi
[ "$FAIL" -eq 0 ]
