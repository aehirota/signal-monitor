# Model assignment notes

v1.0 ships **Sonnet 4.6 everywhere** as the safety default. Nodes
downgrade to Haiku 4.5 only after measured stability.

Same architectural pattern as the cost-discipline ratchet in
account-research-agent (Sonnet for reasoning nodes, Haiku for extraction
nodes, $0.25-0.40/run).

## Downgrade candidates (ranked)

1. **`producer_*` nodes** — mostly mechanical: take search results, extract
   typed signal candidate per Pydantic schema. The reasoning happened
   upstream (Perplexity synthesis, Exa relevance). Strong Haiku candidate.

2. **`orchestrator` node** — purely dispatch logic. Read gaps, decide
   which producer to re-fire. Trivial Haiku candidate.

3. **`critic` node** — judges actionability + type_match + recency_genuine.
   Reasoning-heavy. **Stays Sonnet** until proven otherwise.

4. **`digest_compiler` why_this_matters reasoning** — user-facing intel
   line. The line is what makes the digest readable; cheap to skimp on,
   expensive to skimp. **Stays Sonnet.**

## Downgrade protocol

After 4 weeks of dogfooding (~4 weekly runs):
- For each downgrade candidate: run an A/B with 5 sample runs Sonnet,
  5 sample runs Haiku, same watchlist + same week.
- Score Haiku output against Sonnet output on:
  - Schema validity (0 violations = pass)
  - Signal precision (vs. manual labels)
  - Per-run cost delta
- If Haiku matches Sonnet on precision: downgrade, document with sample
  outputs in this file, ship the change.
- If Haiku fails: keep Sonnet, document the failure mode, revisit next
  Claude release.

## Log

(no downgrades yet — v1.0 ships Sonnet everywhere)
