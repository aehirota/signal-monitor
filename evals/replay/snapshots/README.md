# Replay snapshots

Frozen HTML + expected outputs per signal type. Five per type — three
positives, two negatives. Negatives include at least one hallucination-bait
case (quote attributed to URL where URL doesn't contain the quote) per type
so we deterministically prove the `evidence_traceable` clamp works.

## Layout

```
snapshots/
├── funding_round_retool_2024-09.html
├── funding_round_retool_2024-09.expected.json
├── funding_round_retool_2024-09_negative_hallucinated_quote.html
├── funding_round_retool_2024-09_negative_hallucinated_quote.expected.json
├── exec_move_vercel_2025-11.html
├── exec_move_vercel_2025-11.expected.json
├── ... etc per signal type
└── README.md (this file)
```

## Expected JSON shape

```json
{
  "expected_signal": {
    "type": "funding_round",
    "company_domain": "retool.com",
    "round_type": "series_c",
    "signal_date": "2024-09-XX",
    "amount_usd": 45000000,
    "lead_investor": "..."
  },
  "expected_critic": {
    "evidence_traceable": 10,
    "recency_genuine_min": 7,
    "type_match_min": 7,
    "actionability_min": 7,
    "should_pass": true
  }
}
```

For negative cases (hallucinated quote, stale recency, wrong type):
- `expected_critic.evidence_traceable: 0` (and `should_pass: false`)
- OR `expected_critic.type_match_min: 0`, etc.

Day-2 starts populating with 1 positive + 1 negative for `relevant_jd`
to drive the first end-to-end smoke test. Day-5 backfills the rest.
