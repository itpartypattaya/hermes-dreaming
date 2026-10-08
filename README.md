# hermes-dreaming

![hermes-dreaming — nightly memory consolidation for Hermes Agent](docs/banner.png)

*[Русская версия](README.ru.md)*

Nightly, deterministic **memory consolidation ("dreaming")** for
[Hermes Agent](https://github.com/NousResearch/hermes-agent), in the spirit of OpenClaw dreaming.

An agent's memory goes wrong in two quiet ways: useful facts never make it into durable memory, and
durable memory fills up with stale or duplicated entries until the char limit silently stops all
writes. Every night a Python script looks at what the agent knows and what people actually said, and
wakes the agent only when there is something to decide. The script calls no LLM and never writes
memory; the agent applies its proposals through the regular `memory` tool and reports in a few lines.

## How it works

![One night: inputs, the deterministic pass, the wake gate and the only LLM turn](docs/overview.png)

- **Corroboration from real conversations.** A fact counts only if humans mention it on distinct
  days. Cron prompts, compaction banners, URLs, untrusted chats and the agent's own threads never
  corroborate anything.
- **Scoring** with the OpenClaw weights (relevance, frequency, diversity, recency, consolidation,
  richness) plus a corroboration gate.
- **Matching against memory.** Exact, declarative alias rules and stemmed containment in three
  directions; a candidate that repeats an entry with *different numbers* is a possible update
  (`conflicts`), not a duplicate. Every candidate carries the nearest entry, which file it lives in
  and a verbatim `old_text` anchor for `memory replace`.
- **Screening.** Secrets and prompt-injection are quarantined, dated events expire (RU/EN/Thai
  dates), near-identical facts in the store reach the agent once.
- **A wake gate.** On a quiet night the cron job prints `{"wakeAgent": false}` and the model never
  starts. A memory file close to its char limit counts as work (`memory_pressure`) — the limit gates
  writes silently, so otherwise nobody notices until facts stop being recorded.
- **A loss guard.** If a large share of memory disappears overnight, the next report says so.
- **A diary** (`memories/DREAMS.md`) with provenance for every candidate.

## Every item gets an outcome

![A candidate is written, rejected or comes back; a memory entry is removed or confirmed](docs/lifecycle.png)

Nothing is asked forever. A promotion is either written or put on the reject list (a human's
"outdated" closes it for good, matched by text). New facts, conflicts and decay questions are shown at
most once per 14 days. A memory entry that survived a "still relevant?" question — kept, or rewritten
in place with a fresh date — counts as confirmed and rests for 90 days. Cooldowns start only when
the agent actually answered: a night that failed halfway re-shows the same items.

## Install

Requirements: Hermes Agent ≥ 0.21, Python ≥ 3.9 (standard library only).

**1. The plugin** — a portable Agent Plugins v1 package (`plugin.json` + `skills/dreaming/`):

```bash
hermes plugins install itpartypattaya/hermes-dreaming
hermes plugins enable hermes-dreaming
```

The cron jobs below are bound to this plugin's own skill, so they run exactly the copy you installed
(and the catalog reviewed). Plugin skills are namespaced and kept out of the system prompt's skill
index; the agent finds them with `skills_list` and loads them with `skill_view`.

<details><summary>Alternative: a regular skill</summary>

```bash
hermes skills install itpartypattaya/hermes-dreaming/skills/dreaming
```

The skill then lands in `~/.hermes/skills/dreaming/` and is listed in the skill index. The installer
scripts below use it only when the plugin is not installed; substitute that path in the commands.

</details>

**2. The gates and the config**

```bash
python3 ~/.hermes/plugins/hermes-dreaming/skills/dreaming/scripts/install.py
```

Hermes runs cron scripts only from `~/.hermes/scripts/` (a symlink is refused), so the installer
copies the two gates there, seeds `~/.hermes/dreaming.json` from the example, does a dry run on
throw-away copies of the state, and checks the fact source and the core. It is idempotent;
`install.py --check` verifies without changing anything — run it after every update, since the copies
in `scripts/` can drift from the skill.

```text
== hermes-dreaming installer ==
  ✓ dream-precheck.py in place and identical to the skill copy
  ✓ dream-extract-precheck.py in place and identical to the skill copy
  ✓ config /home/you/.hermes/dreaming.json is valid JSON
  ✓ dry run: fact source: holographic · facts: 0 · human messages in window: 0
  ✓ core gives cron jobs memory natively (Hermes >= 0.21) — the extraction job can use fact_store
  ✓ memory provider 'holographic', fact store present (/home/you/.hermes/memory_store.db)

== 6 ok, 0 warning(s), 0 problem(s) ==
```

**3. Edit the config** — the part nobody can do for you:

```bash
$EDITOR ~/.hermes/dreaming.json    # timezone, trusted_chat_ids, agent_names, fact_source
```

Until `trusted_chat_ids` lists your group chats, **no group chat corroborates memory** (fail-closed
by design). If a trusted chat is a forum, list the threads that are not human talk — the agent's own
alerts, assistant bridges, machine-generated cards — under `excluded_threads`.

**4. The cron jobs**

```bash
~/.hermes/hermes-agent/venv/bin/python \
    ~/.hermes/plugins/hermes-dreaming/skills/dreaming/scripts/install_cron.py --deliver local --dry-run
# happy with the plan? run it again without --dry-run
```

The CLI has no flag for `enabled_toolsets`, and `cron/jobs.json` is live scheduler state, so job
creation goes through the Hermes Python API. The jobs get the plugin skill's qualified name
(`agent-plugin-hermes-dreaming-<hash>:dreaming`) straight from Hermes' plugin registry, so the plugin
must be enabled first; both gates likewise prefer the plugin's copy of `dream.py`. Existing jobs are
skipped; one that loads another skill than this install provides is reported, and `--rebind` points
it at the right one in place. Use
`--deliver telegram:<chat_id>:<thread_id>` to receive the report in a chat and
`--extract-schedule ""` to install the dream alone. The extraction job is cheap to run weekly — a
nightly run mostly answers `[SILENT]` on a small household.

A job keeps the skill name it was created with. The hash in it comes from the plugin's `name` in
`plugin.json`, so updates and reinstalls keep it. Disabling or removing the plugin does not: the job
then runs without its skill, and Hermes starts the nightly report with
`⚠️ Skill(s) not found and skipped`. `install.py --check` names such a job; re-enable the plugin, or
run `install_cron.py --rebind` against the install you kept. The check reads Hermes' plugin
registry, so run it with Hermes' interpreter (`~/.hermes/hermes-agent/venv/bin/python`) — a plain
`python3` only gets a warning that it cannot tell.

### Install from the catalog, not from a URL

```bash
hermes plugins install hermes-dreaming      # the reviewed, pinned catalog entry
```

`hermes plugins install itpartypattaya/hermes-dreaming` installs HEAD as a custom, unreviewed
source, and the next `plugins update` does a `git pull` past the pinned SHA. Same code today,
different trust tomorrow.

### Upgrading from 2.0

2.0 bound the jobs to a regular `dreaming` skill. After installing the plugin, re-run step 2 (the
gates changed) and then step 4 with `--rebind`: the existing jobs switch to the plugin's skill without
losing their schedule, delivery or history.

### Upgrading from 1.x

Version 1 was installed by cloning the repository straight into `~/.hermes/skills/dreaming`. Version
2 keeps the skill in `skills/dreaming/`, so a `git pull` there would hide `SKILL.md` one level down.
Move the old clone away, install with step 1, re-run step 2 (the gates are replaced), and run step 4
with `--rebind` so the existing jobs point at the plugin's skill. Your config and state in `~/.hermes/cache/` stay as they are. The `allow_memory`
flag of 1.x jobs is unused since Hermes 0.21 and can stay or go.

## Writes that wait for a human

If your Hermes runs with `memory.write_approval: true`, a write from a cron session answers
`success: true, staged: true` and the file on disk does not change: the entry sits in
`pending/memory/` until you approve it. The pass reads that queue, so a candidate already waiting
is not offered again, an entry whose removal is queued is not asked about, and a queue that is both
large and old raises a `pending_writes` alert. Two things follow:

- the nightly report says "sent for approval", not "saved" — believe it;
- enabling the **holographic provider** for the fact store opens a second write channel that the
  approval gate does **not** cover: `fact_store` writes to `memory_store.db` directly, including
  from cron, and its prefetch then feeds those facts into ordinary turns. With the gate on, consider
  `fact_source: none` (the nightly memory review keeps working) — `install.py --check` says this out
  loud too.

## Fact sources

The dream consolidates *candidate* facts; where they come from is `fact_source` in `dreaming.json`.

| `fact_source` | where the facts are | extraction job | notes |
|---|---|---|---|
| `holographic` (default) | the Hermes holographic provider's SQLite store (`memory_store.db`, or `plugins.hermes-memory-store.db_path`) | ✅ via `fact_store` | ships with Hermes; enable with `memory.provider: holographic` |
| `sqlite` | any SQLite table: `fact_store_path`, `fact_table`, `fact_columns` | — | for your own export; opened read-only |
| `jsonl` | one JSON object per line: `fact_store_path`, `fact_columns` | — | the simplest export format |
| `none` | no fact store | — | the memory files alone: "still relevant?", fill level, loss guard |

`fact_columns` maps your names onto `id` and `content` (required) and `created_at`, `updated_at`
(ISO or epoch), `category`, `tags` (string or list), `trust` (0..1). Example:
`{"fact_source": "jsonl", "fact_store_path": "exports/facts.jsonl", "fact_columns": {"content": "memory"}}`.

Hosted providers (mem0, Honcho, Supermemory, RetainDB) extract and deduplicate facts on their own
servers; reading them would need API keys in a cron job for little gain, so there is no adapter. If
you still want the dream's corroboration over them, export to `jsonl`.

## Using it by hand

```bash
S=~/.hermes/plugins/hermes-dreaming/skills/dreaming/scripts
python3 $S/dream.py --dry-run                 # what tonight's pass would show; writes nothing
python3 $S/dream.py --explain 42              # why fact 42 scored like that
python3 $S/dream-reject.py 42 --reason "thread was closed"
python3 $S/dream-reject.py --list             # what is muted, and how old
python3 $S/dream-reject.py --from-report --reason "duplicates the system prompt"   # after a manual cleanup
```

Cleaning durable memory by hand? Reject what you dropped: the facts stay in the store, flip to
`in_memory: false`, and the next pass would propose them right back.

### Verify it end to end

```bash
hermes cron list                          # both jobs present and enabled
hermes cron run <dream job id>            # one real run
tail -20 ~/.hermes/memories/DREAMS.md     # a section for today
```

A quiet night prints `{"wakeAgent": false}` and the agent never starts — the wake gate doing its job.

## Configuration

`~/.hermes/dreaming.json` (or `$DREAM_CONFIG`); see `skills/dreaming/examples/dreaming.example.json`.
Missing file → defaults. Precedence: CLI flag > `DREAM_*` env > config > default.

| field | meaning |
|---|---|
| `timezone` | IANA zone for the diary date and day-bucketing of mentions. Not set → Hermes' own `timezone`, then UTC |
| `trusted_chat_ids` | group chats whose messages corroborate memory (**fail-closed**: empty = none) |
| `excluded_threads` | threads of a trusted chat that must not corroborate — `{"<chat_id>": ["<thread_id>", …]}` |
| `untrusted_sources` | session sources that are machine prompts — `cron`, `subagent` and `tool` by default; add `cli` when scripts drive the agent |
| `exclude_patterns` | regexes; a message whose text matches never corroborates (bridge briefs, forwarded digests — machine text with no source of its own) |
| `trust_private_chats`, `trust_sessions_without_chat` | whether a DM, and a session with no chat metadata, count as human speech (both default `true`) |
| `agent_names`, `extra_stopwords` | noise words for the tokenizer |
| `alias_rules` | declarative "same fact, other words" rules — `{"fact": [["a","b"],["c"]], "memory": [["x"]]}` |
| `fact_source`, `fact_store_path`, `fact_table`, `fact_columns` | where candidate facts come from (above). Env: `DREAM_FACT_SOURCE`, `DREAM_FACT_STORE` |
| `durable_memory_paths` | files that already are memory (dedupe targets) |
| `memory_char_limits` | char limits of MEMORY.md / USER.md. **Leave it out** and the limits are read from Hermes' own `memory.memory_char_limit` / `user_char_limit`; the example's numbers are the core defaults and will misreport an install that raised them |
| `pending.dir`, `pending.alert_min`, `pending.alert_days` | the approval queue (`pending/memory`): when ≥`alert_min` writes have waited longer than `alert_days`, one alert is raised |
| `pinned_markers` | entries with these markers are never asked about |
| `windows`, `gates`, `weights` | scoring knobs — `references/scoring.md`; `gates.md_confirmed_cooldown_days` (90) is the rest period of a confirmed entry |
| `precheck.memory_full_pct` | fill level (%) at which a memory file alone wakes the agent (default 85; `0` disables) |
| `precheck.timeout_sec` | how long the gate waits for `dream.py` before reporting `dream_error` (default 600) |
| `extract.*` | extraction chunking: `max_messages` 200, `max_chars` 40000, `min_messages` 15, `backfill_days` 60, `max_retries` 3 |
| `diary.heading`, `diary.keep_sections`, `diary.path` | diary header, rotation and where it lives (`memories/DREAMS.md`) |
| `memory_loss_alert_fraction` | loss-guard threshold (0.25) — applied to the share of lost entries **and** of lost characters |
| `gates.promotion_cooldown_days` | rest for a promotion already shown (3 days); without it a candidate the agent could not write came back every night |

## Safety and privacy

![Files the skill reads and writes](docs/files.png)

- **Network:** none. **LLM calls:** none from the scripts; the only model turn is the agent in the
  cron session, with your model and your tools.
- **Reads:** `state.db` (sessions and messages, opened read-only), the fact store (read-only), the
  memory files.
- **Script writes:** `~/.hermes/cache/dream*.json` (cooldowns, reject list, snapshot, extraction
  cursor; mode 0600), `~/.hermes/memories/DREAMS.md` (diary), and — only `install.py` — the two gate
  copies in `~/.hermes/scripts/` plus a seeded `dreaming.json`.
- **The agent writes** memory entries through the `memory` tool (at most 6 changes a night) and, in
  the extraction job, candidate facts through `fact_store`. Nothing is ever deleted from the store.
- Message and fact contents are treated as data, never as instructions. Facts, the evidence quoted
  next to them, the messages the extraction job hands over and the "still relevant?" previews are
  all screened for credentials and injection directives; a hit is quarantined (a secret's text is
  published nowhere, an injection keeps a 60-char preview in the full JSON only).
  **The screening is a regex filter, not a guarantee:** it knows the common key shapes
  (`sk-`, `gsk_`, `ntn_`, `gho_`, AWS, Google, Slack, Telegram, JWT, PEM), credential markers in
  Russian and English, and the injection phrasings the core's own cron scanner looks for — a
  wording nobody has seen yet can still get through. Keep the approval gate on if that matters.
  No tools, hooks, middleware or environment variables are registered.

## Repository layout

```text
plugin.json                          Agent Plugins v1 manifest
skills/dreaming/
  SKILL.md                           agent instructions
  scripts/dream.py                   the pass (never writes memory)
  scripts/dream-precheck.py          nightly wake gate        → ~/.hermes/scripts/
  scripts/dream-extract-precheck.py  extraction gate          → ~/.hermes/scripts/
  scripts/dream-reject.py            reject list CLI
  scripts/install.py                 installs and verifies the gates
  scripts/install_cron.py            creates both cron jobs
  references/scoring.md              signals, gates, sections, state files
  references/cron-memory.md          what cron sessions may touch
  examples/                          config and cron job examples
tests/                               stdlib unittest, synthetic data, no network
docs/                                README images (SVG sources in docs/src/)
```

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests also run from a copy that keeps `tests/` next to the skill directory. Issues and pull
requests are welcome.

## Design notes

- Corroboration must come from **humans**; machine text never counts.
- **Every candidate needs an outcome.** What the agent cannot close at night (there is no terminal
  in the cron session) is closed by cooldowns on the script side, not by agent discipline.
- **Text, not ids.** SQLite reuses ids; state is keyed by a text fingerprint, so a re-extracted fact
  is recognised.
- **Zero writes is a success.** A night is capped at 6 memory changes, and `replace` is used only
  with a verbatim anchor the script hands over.
- Borrowed ideas: OpenClaw (phases, weights, gates, loss fraction, explain), mem0 (nearest entry →
  update instead of append), Graphiti (supersession as a flag), Generative Agents (evidence
  citations), MemoryBank (decay by strength).

## Author

Anton Vaskov — Telegram [@passone](https://t.me/passone), GitHub [itpartypattaya](https://github.com/itpartypattaya).

## License

[MIT](LICENSE)
