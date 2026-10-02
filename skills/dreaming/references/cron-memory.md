# Cron sessions and memory

Two of this skill's moving parts need the agent to touch memory from a **cron** session: the nightly
dream writes promotions through the `memory` tool, and the optional extraction job stores candidate
facts through the memory provider's `fact_store` tool. This page says what Hermes allows.

## Hermes 0.21 and later (required)

Since Hermes 0.21.0 (v2026.8.31, PR #91447) cron agents load and update persistent memory like every
other agent (`skip_memory=False` in `cron/scheduler.py`). So, out of the box:

- ✅ **the nightly dream works.** It needs only the `memory` tool, and its job declares the `memory`
  toolset;
- ✅ **the extraction job works** with the holographic provider: `fact_store` is registered in the
  cron session like in any chat.

`scripts/install.py --check` confirms it with the line:

```
  ✓ core gives cron jobs memory natively (Hermes >= 0.21) — the extraction job can use fact_store
```

This skill never modifies Hermes itself. Both jobs run with `enabled_toolsets: ["memory"]` only — no
terminal, no web — and everything they are handed is framed as data, not instructions.

## Older cores

Before 0.21 every cron job started with `skip_memory=True`. The built-in file store (`MEMORY.md` /
`USER.md`, the `memory` tool) was still reachable when the job declared the `memory` toolset, but the
memory **provider** and its `fact_store` tool were not registered at all. On such a core the dream
works and extraction cannot: either upgrade Hermes, or install the jobs without extraction:

```bash
python3 scripts/install_cron.py --deliver local --extract-schedule ""
```

The dream then consolidates whatever reaches the fact store by other means — the `memory add` mirror
and any `fact_store` calls the agent makes in normal chats — and stays quiet rather than inventing
work when there is little.

## Verifying

```bash
python3 scripts/install.py --check
hermes cron run <extraction job id>            # one real run
grep fact_store ~/.hermes/logs/agent.log | tail -3
```
