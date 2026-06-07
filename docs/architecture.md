# Architecture

Full walkthrough of the Signal Monitor state machine. Companion reading to
the README; that page sells the system, this page explains how it works.

## High-level

LangGraph state machine, Anthropic SDK direct for leaf nodes. Six typed
signal producers run as a parallel super-step. A per-signal critic with a
code-enforced evidence-traceability clamp gates which signals survive.
Failed signals trigger a selective re-fire via the orchestrator using
LangGraph's `Send` API — only the specific producer that failed re-runs,
with a hint to use the fallback source. Survivors hit a SQLite-backed
identity-key deduper. What's new + still inside its recency window goes
into the weekly digest.

```
                       ENTRY
                         │
                    ┌────▼────┐
                    │ loader  │  watchlist + dedup state + recency windows
                    └────┬────┘
                         │
        ┌──────────┬─────┴─────┬──────────┐
        ▼          ▼           ▼          ▼          ▼          ▼
  funding_round exec_move  relevant_jd product_launch tech_signal news_strategic
   (Exa)        (Exa)       (Tavily)    (Perplexity)  (Exa)      (Perplexity)
        │          │           │          │           │           │
        └──────────┴─────┬─────┴──────────┴───────────┴───────────┘
                         │  merge_signals reducer
                    ┌────▼────┐
                    │ critic  │  evidence_traceable (code) + 3 LLM dims
                    └────┬────┘
                         │
              ┌──────────┴───────────┐
              ▼                      ▼
         gaps exist               no gaps
         iteration < max          (or iteration == max)
              │                      │
       ┌──────▼───────┐               │
       │ orchestrator │               │
       │   Send(...)  │               │
       └──────┬───────┘               │
              │ re-fire specific      │
              │ producer w/ fallback  │
              ▼                      │
       (back to producer)            │
              │                      │
              ▼                      │
            critic ─────► ... ─────► │
                                     ▼
                              ┌─────────────┐
                              │   deduper   │  identity_key against SQLite
                              └──────┬──────┘
                                     │
                              ┌──────▼───────┐
                              │ digest_compiler │
                              └──────┬───────┘
                                     │
                                     ▼
                                    EXIT
```

## State

The graph state is `state.GraphState` (TypedDict). Reducer-managed fields:

- `signal_candidates: Annotated[list[AnySignal], merge_signals]` — six
  producers fan out concurrently and all write to this field. Default
  LangGraph channel (`LastValue`) would clobber 5 of 6 writes. `merge_signals`
  concatenates. Same architectural lesson as account-research-agent's
  `merge_raw`.

- `spend: Annotated[SpendLedger, merge_spend]` — same shape: each leaf
  node returns its own spend slice; the reducer sums into a single ledger
  that the digest compiler renders for accountability.

Single-writer fields (no reducer needed): `config`, `watchlist`,
`dedup_keys_active`, `critique`, `signals_after_dedup`, `digest_html`,
`digest_text`, `run_id`, `iteration`.

## Nodes

### `loader`

Reads `watchlist.yaml` and `config.yaml`. Queries SQLite for `dedup_keys_active`
(identity_keys still inside their type's recency window). Initializes
`run_id`, `iteration=0`, `spend` with the per-run cap. Creates the per-run
audit directory at `.tmp/runs/<run_id>/`. Honors `.tmp/PAUSE`.

### `producer_<type>` (six nodes, parallel super-step)

One node per signal type. Each iterates the watchlist (respecting
`mute_types`), queries its primary source for that type, parses results
into typed Pydantic signals via `signal_class_for(signal_type)`, and
returns 0..N candidates plus a spend slice.

On re-fire (when iteration > 0 because the critic re-dispatched), the
producer reads the orchestrator's hint from local node state and uses the
fallback source (Anthropic native `web_search`) instead of the primary.

Cost discipline: per-company soft cap stops a producer from blowing budget
on a single company; per-run hard cap raises `BudgetExceeded` and aborts
the call.

### `critic`

For each candidate signal:

1. **Code-enforced `evidence_traceable` check (`tools/evidence_check`)** —
   fetches `source_url`, renders to text, substring-matches `evidence_quote`.
   Score 0 or 10. Hallucinated quotes attributed to real URLs cannot pass.
2. If `evidence_traceable == 0`: skip the LLM step, mark failed, emit a Gap.
   (Cost discipline — no point asking the LLM about hallucinated evidence.)
3. Otherwise, Sonnet scores `recency_genuine`, `type_match`, `actionability`
   (0-10 each) for the company's full batch of candidates in one call.

Pass criteria: ALL four dimensions >= 7 AND `evidence_traceable == 10`.

### `orchestrator` + `should_retry`

`should_retry` is the conditional edge after `critic`:
- Returns `"retry"` if any gaps remain AND `iteration < max_retries_per_type`.
- Returns `"done"` otherwise.

`orchestrator_node` runs only on the `"retry"` branch. It reads
`critique.gaps`, then for each gap emits a LangGraph `Send` to
`producer_<failed_signal_type>` with the hint embedded in node-local
state ("use fallback source"). The producer re-runs only for that type and
that company. Unfilled gaps after `max_retries_per_type` become audit-log
entries, not failures.

Same selective-dispatch pattern as account-research-agent's
`Gap.target_node`: the orchestrator routes to specific nodes per gap, not
broadcasts to every node.

### `deduper`

For each signal in `critique.signals_passed`:
- Compute `signal.identity_key()` (each typed signal owns its own key).
- If key in `dedup_keys_active` → drop (already in the audit log).
- Else → keep, and write the new row to SQLite.

Recency expiry is enforced by `loader_node` when it computes
`dedup_keys_active` — not here. That way, a signal that exits its window
naturally drops out of active dedup state.

### `digest_compiler`

For each surviving signal, asks Sonnet for a one-sentence `why_this_matters`
line. Formats into the locked block layout. Sorts by company priority
then signal_date desc. Sends via Resend.

If zero signals: digest still ships with "No new signals across N
companies — system healthy." Silence is a failure mode.

## Three code-enforced rules

These are non-negotiable architectural commitments. They are what
makes the system trustworthy as an unattended runtime, not just a demo.

1. **`evidence_traceable` clamp** (`tools/evidence_check`). Quote must
   appear in the fetched page. Code-enforced; LLM cannot reason around it.

2. **Spend cap** (`tools/spend_tracker`). Per-call charge optimistically
   before the API call; raise `BudgetExceeded` if cap would be exceeded.
   Soft per-company cap stops one bad company from starving the rest.

3. **Identity-key dedup** (`storage/sqlite_store`). Each signal type owns
   its uniqueness contract. URL collisions don't count; identity collisions
   do.

This is the third instance of the code-over-prompt pattern across the
portfolio (after the disqualifier clamp in `account-research-agent` and
the length-compliance gate in the blog autopilot). When rules matter, they
get enforced in code, not in prose.
