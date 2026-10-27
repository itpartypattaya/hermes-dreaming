# Task outcome reports (optional holographic capability)

Holographic's opt-in experience journal keeps a receipt of the exact fact versions returned by search/prefetch,
then records task outcomes separately from truth/trust. Dreaming can read that journal without another model,
without replaying search, and without creating a cache of corrections or lessons.

From a manual terminal session, using the skill's `scripts/dream.py`:

```text
python dream.py --memory-outcomes
python dream.py --memory-explain ATTEMPT_ID
python dream.py --memory-explain ATTEMPT_ID --memory-format text
```

`--hermes-home` and `--config` select the same home/config as other dreaming commands. The default report reads the
profile's configured holographic store. For one scoped store, specify `--memory-store STORE_KEY`; that key must be
explicitly present in `chat_stores.promote_from`. `read: true` alone does not grant access, stores are never joined,
and symlinked store files are refused. Missing/unapproved keys do not reveal other stores' contents.

JSON status is `ok`, `not_recorded`, `unsupported`, `unsupported_scope` or `unavailable`; a non-ok explicit command
exits 1. A legacy provider without the experience schema is unsupported, not a successful empty journal. Busy,
corrupt or inaccessible databases are unavailable. No missing database file is created. Reads use a single SQLite
read-only snapshot and close the connection afterwards.

Summary groups use the reason code and count one active outcome per attempt. Revisions such as dead_end → corrected
→ useful remain one attempt. A single report is labelled single; multiple reports remain agent reports; different
outcomes stay mixed. None are treated as independent human confirmations, true/false votes, or automatic lessons.
There is no conversion to helpful/unhelpful, no trust change and no automatic MEMORY.md / USER.md promotion.

Explain shows the stored retrieval stages and emitted fact/version ids, plus the outcome history. It never repeats
a past search to guess what happened. Detailed stages may be absent because tracing was disabled. Full correction
text and the task label remain in the holographic journal, available through its `fact_experience explain`; dreaming
does not copy them to its payload, diary or state. Fact version/source details remain available in holographic history.

Optional `"task_outcomes": true` in `dreaming.json` adds only summary counts/enums to the ordinary nightly result.
The default is off. This adds no separate model wakeup and does not alter the ordinary work decisions. It reads the
profile store only; scoped reports require the explicit manual store choice above. Summary counts are a snapshot,
not a durable list of facts or a basis for an automated action.

There is no second text store to erase or replay. Provider deletion triggers remove receipts and their outcomes;
the next live report cannot read those records. Expired attempts are excluded using the provider's 30-day contract.
Downloaded reports, terminal logs and external database backups are not managed copies. If persistent lessons are
added later, they require their own dependencies, deletion notification/acknowledgement and fail-closed reads.

This optional reader needs provider capability `experience_schema=1`. On an older provider the rest of dreaming
works normally. This capability is released in holographic 0.20.0 and dreaming 3.6.0; deploy compatible
holographic before enabling collection, then install the dreaming reader. Do not claim token savings or answer
quality improvements from the existence of the journal alone.
