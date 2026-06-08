# JSON API contract

Stable machine-readable output for downstream consumers (e.g. `meeting-prep-agent`).

## Invocation

```bash
# single-company JSON (most common subprocess use case)
python run.py --only vercel.com --json

# full watchlist JSON (rare; mostly for audit tooling)
python run.py --json
```

- Stdout: the JSON payload below (and **only** the JSON payload).
- Stderr: all diagnostic prints (orchestrator/critic/producer logs, audit-write notices, Resend status).
- Exit code: `0` on success, non-zero on uncaught error.
- `--json` implies `--dry-run` — JSON consumers should not trigger Resend email sends.
- File side effects: still writes `.tmp/runs/<run_id>/run.json` audit log and SQLite dedup updates so the audit trail is preserved regardless of invocation mode.

## Schema (v1.0)

```jsonc
{
  "schema_version": "1.0",                       // bump on breaking changes
  "run_id": "2026-06-07T16-32-48Z",              // ISO timestamp, also the .tmp/runs/ dir name
  "watchlist_size": 5,                            // total companies in this run's watchlist
  "signals_count": 6,                             // len(signals)
  "signals": [
    {
      // ─── Common fields (every signal carries these) ────────────
      "company_domain": "vercel.com",
      "type": "relevant_jd",                      // see SignalType section below
      "signal_date": "2026-06-01",
      "source_url": "https://vercel.com/careers/...",
      "evidence_quote": "We're hiring a GTM Engineer to ...",
      "confidence": 0.95,                          // 0.0–1.0
      "raw_source": "tavily",                      // exa | tavily | perplexity | anthropic_web_search

      // ─── Type-specific fields (see SignalType section) ────────
      "role_title": "GTM Engineer",
      "role_title_normalized": "gtm engineer",
      "location": "US Remote",
      "job_id": null
    }
    // ... more signals
  ],
  "digest_text": "Subject: Signal Monitor — 6 new signals...\n\n...",  // full rendered digest with why_this_matters reasoning
  "spend_total_usd": 0.56,
  "spend_cap_usd": 4.0
}
```

## Signal types and their type-specific fields

The taxonomy IS the contract. Six concrete types in v1; each surfaces additional fields beyond the common base.

| `type` | Type-specific fields |
|---|---|
| `funding_round` | `round_type` (seed / series_a / ... / series_e / late_stage / bridge / debt / ipo), `amount_usd` (int or null), `lead_investor` (string or null) |
| `exec_move` | `role` (normalized: "COO", "VP Sales", "Head of RevOps"), `person_name`, `move_kind` (hire / promotion / departure / interim) |
| `relevant_jd` | `role_title`, `role_title_normalized`, `location` (string or null), `job_id` (string or null) |
| `product_launch` | `product_name`, `launch_quarter` (e.g. "2026-Q2") |
| `tech_signal` | `tech_mentioned` (lowercased; e.g. "salesforce", "clay"), `signal_action` (evaluating / migrating_to / migrating_off / adopted / deprecated) |
| `news_strategic` | `event_type` (m_and_a / ipo_file / layoffs / reorg / leadership_shakeup) |

Schemas defined in [`schemas/signal_types.py`](../schemas/signal_types.py). Each subclass `BaseSignal`; pydantic `.model_dump(mode="json")` is what populates the JSON.

## Schema stability

Bump `schema_version` on:

- Removing or renaming a field
- Changing a field's type (e.g. `int` → `string`)
- Changing the semantics of a value (e.g. score range)
- Adding a new `type` value to the `SignalType` enum (since consumers may need to handle the new type)

Adding new optional fields on existing types does not require a bump — consumers should ignore unknown fields.

## Consumer guidance

Downstream consumers (e.g. `meeting-prep-agent`) should:

1. Pin to a tagged release of this repo (e.g. `v1.1.0`) rather than `main` — schema changes between releases are version-controlled.
2. Read `schema_version` and refuse to proceed if it exceeds the version they were written against.
3. Use `signals[].type` to dispatch type-specific rendering.
4. Treat `digest_text` as opaque human-readable text (for embedding directly in their own brief without re-rendering).
5. Each signal in `signals[]` has already passed the `evidence_traceable` clamp — the quote was verified against the live URL. Consumers don't need to re-verify.

## Example

```bash
# from meeting-prep-agent, fetching live signals for vercel.com:
out=$(python /path/to/signal-monitor/run.py --only vercel.com --json 2>/tmp/sm.log)
echo "$out" | jq '.signals | map(select(.type == "relevant_jd"))'
echo "$out" | jq '.spend_total_usd'
```
