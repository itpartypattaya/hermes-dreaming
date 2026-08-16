# hermes-dreaming

Nightly, deterministic **memory consolidation ("dreaming")** for
[Hermes Agent](https://github.com/NousResearch/hermes-agent) — in the spirit of
OpenClaw dreaming, without an LLM in the cron loop.

Every night a Python script reads the agent's fact store (`memory_store.db`,
holographic memory plugin) and the transcripts (`state.db`), and works out what
deserves durable memory:

- **corroboration from real conversations** — a fact counts only if humans
  actually mention it, on distinct days (URLs, harness banners, cron prompts and
  untrusted chats never corroborate);
- **scoring** with the OpenClaw weights (relevance / frequency / diversity /
  recency / consolidation / richness) plus a corroboration gate;
- **dedupe** against `MEMORY.md` / `USER.md` — exact, declarative alias rules,
  stemmed containment in three directions (including "the entry I wrote from
  this candidate last night");
- **ready-to-use edit anchors** — each candidate carries the nearest durable
  entry, which file it lives in, and a verbatim `old_text` for `memory replace`,
  so the agent updates an entry instead of guessing at it;
- **conflicts** — a candidate that repeats an existing entry with different
  numbers/dates is flagged as a possible update, not swallowed as a duplicate;
- **quarantine** of secrets and prompt-injection, **expiry** of dated events
  (RU/EN/Thai dates), **decay** questions ("still relevant?") for facts and
  memory entries, **pinned** entries;
- **outcomes**: a reject list ("outdated" from a human closes the question for
  good), display cooldowns (nothing is asked twice within 14 days), a snapshot
  **loss guard** (alert when a large share of memory disappears overnight);
- **wake gate**: the agent (and its tokens) wakes only when there is work;
- a dream **diary** with provenance for every candidate;
- an optional **extraction job** (`dream-extract-precheck.py`) that hands fresh
  human messages to the agent so it stores *candidate* facts with `fact_store`
  — the stock holographic store does not grow by itself, and without candidates
  the dream has nothing to consolidate.

The script **never writes memory itself**. It emits JSON; the agent applies
promotions through the regular `memory` tool and reports to the human in ≤6
lines. Humans stay in the loop.

## Install

Requirements: Python ≥ 3.9 (stdlib only) and a Hermes install. Everything else
is optional — but it decides how much of the skill you actually get:

| your Hermes | promotions, new facts, conflicts, decayed facts | MEMORY.md "still relevant?", loss guard, diary, memory usage | extraction job |
|---|---|---|---|
| stock (`memory.provider: ""` — the default) | — no fact store to consolidate | ✅ | — no `fact_store` tool |
| `memory.provider: holographic` | ✅ | ✅ | ✅ in chats, ✗ in cron |
| + the cron patch ([`docs/cron-memory.md`](docs/cron-memory.md)) | ✅ | ✅ | ✅ |

The holographic provider **ships with Hermes** (a local SQLite fact store, no
extra dependencies); it is simply off by default. Without it the pass still runs
and still reviews your curated `MEMORY.md` — it just has no candidates to
promote, and says so instead of pretending to work. Any other store exposing the
same `facts` table works too.

```bash
# 1. the skill
git clone https://github.com/itpartypattaya/hermes-dreaming ~/.hermes/skills/dreaming

# 2. gates into place, config seeded, install verified
cd ~/.hermes/skills/dreaming && ./install.sh
```

`install.sh` copies both pre-check scripts into `$HERMES_HOME/scripts` (Hermes
runs cron scripts only from there — a symlink is refused), seeds
`~/.hermes/dreaming.json` from the example if you have none, does a sandboxed
dry run, and tells you whether this core lets a cron job reach the fact store.
It is idempotent; `./install.sh --check` verifies without changing anything —
worth running after every `git pull`, since the copies in `scripts/` can drift.

Expected output on a healthy install:

```
== hermes-dreaming installer ==
   skill:       /home/you/.hermes/skills/dreaming
   HERMES_HOME: /home/you/.hermes
  ✓ dream-precheck.py in place and identical to the skill copy
  ✓ dream-extract-precheck.py in place and identical to the skill copy
  ✓ config /home/you/.hermes/dreaming.json is valid JSON
  ✓ dry run: [dream] facts:0 messages:0 promote:0 … themes:0 quarantine:0 …
  ✓ core honours per-job allow_memory — the extraction job can use fact_store
  ✓ memory provider 'holographic', fact store present (/home/you/.hermes/memory_store.db)

== 6 ok, 0 warning(s), 0 problem(s) ==
```

Zeros on a fresh install are correct, not a failure: the dream has nothing to
consolidate yet and stays quiet.

```bash
# 3a. turn on a fact store, unless you already have one (see the table above)
$EDITOR ~/.hermes/config.yaml      # memory: { provider: holographic }

# 3b. edit the skill config — this is the part nobody can do for you
$EDITOR ~/.hermes/dreaming.json    # timezone, trusted_chat_ids, agent_names, aliases
```

Until `trusted_chat_ids` lists your chats, **no group chat corroborates
memory** (fail-closed by design) and the dream will promote almost nothing.

```bash
# 4. the cron jobs
~/.hermes/hermes-agent/venv/bin/python \
    ~/.hermes/skills/dreaming/scripts/install_cron.py --deliver local --dry-run
# happy with the plan? run it again without --dry-run
```

The CLI (`hermes cron add`) has no flags for `enabled_toolsets` or
`allow_memory`, and `cron/jobs.json` is live scheduler state that must not be
hand-edited — so job creation goes through the Python API, which is what
`install_cron.py` is. It skips jobs that already exist, so re-running is safe.
Use `--deliver telegram:<chat_id>:<thread_id>` to get the report in a chat, and
`--extract-schedule ""` to install the dream alone.

⚠️ **The extraction job needs a core that honours `allow_memory`.** Stock
Hermes runs cron with `skip_memory=True`, which keeps `fact_store` out of the
session — the nightly dream is unaffected, extraction is not. Your options (a
three-line core patch with a script, or living without extraction) are in
[`docs/cron-memory.md`](docs/cron-memory.md).

### Verify it end to end

```bash
hermes cron list                                  # both jobs present, enabled
hermes cron run <dream job id>                    # one real run
tail -20 ~/.hermes/memories/DREAMS.md             # a section for today
grep -E "tool memory|fact_store" ~/.hermes/logs/agent.log | tail -3
```

A quiet night prints `{"wakeAgent": false}` and the agent never starts — that is
the wake gate doing its job, not a failure.

### Useful by hand

```bash
python ~/.hermes/skills/dreaming/scripts/dream.py --out ~/.hermes/cache/dream.json --diary ~/.hermes/memories/DREAMS.md
python ~/.hermes/skills/dreaming/scripts/dream.py --explain 42     # why did fact 42 score like that
python ~/.hermes/skills/dreaming/scripts/dream-reject.py 42 --reason "thread was closed"
python ~/.hermes/skills/dreaming/scripts/dream-reject.py --list    # what is muted, and how old
```

Tests: `python -m unittest discover -s tests` (from the skill directory; stdlib
`unittest`, synthetic data).

## Configuration

`~/.hermes/dreaming.json` (or `$DREAM_CONFIG`). Missing file → defaults;
precedence CLI flag > `DREAM_*` env > config > default. Key fields:

| field | meaning |
|---|---|
| `timezone` | IANA zone for the diary date and day-bucketing of mentions |
| `trusted_chat_ids` | group chats whose messages corroborate memory (**fail-closed**: empty = none) |
| `agent_names`, `extra_stopwords` | noise words for the tokenizer |
| `alias_rules` | declarative "same fact, other words" rules — `{"fact": [["a","b"],["c"]], "memory": [["x"]]}`; `#437` = whole number |
| `profile_hint_terms`, `profile_categories` | hints that a fact belongs to the user profile |
| `durable_memory_paths` | files that already are memory (dedupe targets) |
| `diary.heading`, `diary.keep_sections` | diary section header and rotation |
| `windows`, `gates`, `weights` | scoring knobs (see `references/scoring.md`) |
| `memory_char_limits` | to report fill level of MEMORY.md / USER.md |
| `pinned_markers` | entries with these markers are never asked about |
| `memory_loss_alert_fraction` | loss-guard threshold (default 0.25) |
| `precheck.actionable_keys`, `precheck.max_content` | what wakes the agent, prompt trimming |
| `extract.*` | extraction chunking: `max_messages` 200, `max_chars` 40000, `min_messages` 15, `backfill_days` 60 |

## Files

```
SKILL.md                          agent instructions (loaded by Hermes)
install.sh                        put the gates in place; --check verifies an install
scripts/dream.py                  the pass (read-only by memory)
scripts/dream-precheck.py         cron wake gate            → ~/.hermes/scripts/
scripts/dream-extract-precheck.py extraction gate           → ~/.hermes/scripts/
scripts/install_cron.py           creates both cron jobs (what the CLI cannot)
scripts/patch_cron_memory.py      optional core patch: per-job memory opt-in
scripts/dream-reject.py           reject list CLI
docs/cron-memory.md               what cron sessions may touch, and the patch
references/scoring.md             signals, gates, sections, state files
examples/                         config and cron job examples
tests/                            unittest suite
```

State (all 0600, fail-soft): `cache/dream.json`, `cache/dream-asked.json`,
`cache/dream-seen.json`, `cache/dream-rejected.json`,
`cache/dream-snapshot.json`, `cache/dream-extract-state.json`, `memories/DREAMS.md`.

## Design notes

- Corroboration must come from **humans**. Cron prompts, compaction banners,
  skill-injection envelopes, image descriptions and URLs are not speech.
- **Every candidate needs an outcome.** Anything the agent cannot close at
  night (no terminal in the cron session) is closed by cooldowns on the script
  side, never by agent discipline.
- **Text, not ids.** SQLite reuses `fact_id`; every state file is keyed by a
  text fingerprint, so a re-extracted fact is recognised.
- **Zero writes is a success.** The SKILL caps a night at 6 memory changes and
  3 new entries, and `replace` is used only with a verbatim anchor the script
  hands over; the loss guard catches the opposite failure.
- Borrowed ideas: OpenClaw (phases, weights, gates, loss fraction, explain),
  mem0 (nearest entry → update instead of append), Graphiti (supersession as
  a flag, not deletion), Generative Agents (evidence citations), Claude Code
  auto-dream (absolute dates, index budget), MemoryBank (decay by strength).

## License

MIT — see `LICENSE`.
