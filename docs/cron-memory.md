# Cron sessions and memory: what stock Hermes allows

Two of this skill's moving parts need the agent to touch memory from a **cron**
session, and Hermes treats cron as stateless by default. This page says exactly
what works out of the box, what does not, and what to change if you want the
extraction job.

## The rule in the core

`cron/scheduler.py` starts every job with `skip_memory=True`. In
`agent/agent_init.py` that flag gates two different things:

| what | gated by | effect in cron |
|---|---|---|
| built-in file store (`MEMORY.md` / `USER.md`, the `memory` tool) | `if not skip_memory or "memory" in enabled_toolsets` | **available** when the job declares `enabled_toolsets: ["memory"]` |
| external memory **provider** (holographic etc.) and its tools `fact_store` / `fact_feedback` | `if not skip_memory` | **unavailable** — nothing registers those tools |

So on a **stock** install:

- ✅ **the nightly dream works.** It only needs the `memory` tool to write
  promotions, and the job declares the `memory` toolset.
- ❌ **the extraction job cannot do its work.** It is told to call `fact_store`,
  and in a stock cron session that tool does not exist. The agent wakes, reads
  the messages, and has nothing to store them with.

`install.sh` checks this and warns; the installer sets `allow_memory: true` on
both jobs, which a patched core reads and a stock core simply ignores.

## Option A — skip extraction (no core changes)

Install with `--extract-schedule ""`. The dream then consolidates whatever
reaches the fact store by other means: the `memory add` mirror, and any
`fact_store` calls the agent makes in normal (non-cron) chats. On a young
install that can be very little — the dream will be honest about it and stay
quiet rather than invent work.

## Option B — let opted-in jobs use the provider (three-line patch)

Make `skip_memory` a per-job decision instead of a constant. In
`cron/scheduler.py`:

```python
# before
            skip_memory=True,  # Cron system prompts would corrupt user representations

# after
            # Cron stays stateless by default. A narrowly trusted maintenance
            # job (here: dreaming) may opt in with allow_memory=true.
            skip_memory=not bool(job.get("allow_memory", False)),
```

`scripts/patch_cron_memory.py` in this repo applies exactly that, idempotently:

```bash
python3 scripts/patch_cron_memory.py            # defaults to ~/.hermes/hermes-agent
python3 scripts/patch_cron_memory.py --check    # is it applied?
python3 scripts/patch_cron_memory.py --revert   # put the original line back
```

It refuses to touch a file it does not recognise, byte-compiles the result
before writing, and keeps a `.bak` next to the original.

### What you are opting into

The upstream default exists for a reason: a cron prompt is not a person, and a
job that writes memory can pollute the user's profile with its own instructions.
That is why the opt-in is **per job** and why this skill's jobs are narrow:

- both jobs run with `enabled_toolsets: ["memory"]` only — no terminal, no web;
- the extraction job is told, in its prompt, to write **only** to the fact store
  and never to `MEMORY.md`/`USER.md`;
- the dream applies at most a handful of changes per night and reports each one;
- `SKILL.md` treats all message content as data, never instructions, and the
  pass quarantines secrets and injection attempts before the agent sees them.

After a Hermes upgrade the patch is gone (the file is replaced) — re-run the
script, or add it to whatever you already use to re-apply local core changes.

## Verifying

```bash
./install.sh --check
```

Look for either of these lines:

```
  ✓ core honours per-job allow_memory — the extraction job can use fact_store
  ! core runs cron with skip_memory=True: the nightly dream works, but the extraction
```

To see it end to end, trigger one run and read the log:

```bash
hermes cron run <extraction job id>
grep fact_store ~/.hermes/logs/agent.log | tail -3
```
