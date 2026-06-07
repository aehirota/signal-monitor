# Signal Monitor — project context for Claude Code

## What this is

A LangGraph agent that scans a watchlist of companies for typed buying-window signals and emails a weekly digest. Companion to `account-research-agent` (modular, not coupled).

## Architectural through-line

This is the **third** of Anderson's shipped agentic systems. The portfolio thesis depends on architectural coherence across all three:

| Pattern | account-research-agent | blog-autopilot | signal-monitor |
|---|---|---|---|
| Producer + critic gate | ✓ critic emits typed Gaps | ✓ critic blocks bad posts | ✓ per-signal critic with hard threshold |
| Code-enforced rule layer | ✓ disqualifier clamp | ✓ length compliance | ✓ `evidence_traceable` clamp |
| LangGraph state machine + `Send` re-dispatch | ✓ `Gap.target_node` | — | ✓ orchestrator routes failed types to fallback |

When making implementation decisions: **match the pattern, don't reinvent.** If something here looks different from `account-research-agent` without a documented reason, that's a smell.

## Six signal types (v1 — locked)

`funding_round`, `exec_move`, `relevant_jd`, `product_launch`, `tech_signal`, `news_strategic`.

Recency windows: 120 / 90 / 45 / 60 / 30 / 60 days respectively. Beyond the window, signals stop surfacing — they live in the audit log forever, never re-emerge.

## Source assignment

Primary: Exa (funding, exec, tech), Tavily (jd), Perplexity (product, news).
Fallback (universal): Anthropic native `web_search` tool. Zero new infra.

Each producer queries its **primary only**. The critic re-fire (via orchestrator) triggers the fallback. No parallel fan-out across sources — that's a v2 question if recall is the bottleneck.

## Code-enforced rules (non-negotiable)

1. **Evidence traceability.** Python fetches the URL, renders to text, greps for the quoted snippet. If absent → score 0 → automatic fail. The LLM cannot talk its way past it.
2. **Spend cap.** `SpendTracker` wraps every API call. Once `total_spent >= per_run_cap`, further calls return `BudgetExceeded`.
3. **Identity-key dedup.** Each signal type has an `identity_key()` method on its Pydantic model. SQLite stores `(company, type, identity_key, first_seen_at, latest_url, status)`. New signal with existing active key inside its recency window → dropped.
4. **Max 1 critic retry per failed type per company per run.** Hard-coded ceiling, not configurable. Prevents infinite loops.

## Model assignment (v1.0)

Ship **Sonnet 4.6 everywhere** initially. Downgrade nodes to Haiku 4.5 only after measured output stability (~4 weeks of dogfooding). Document the downgrade rationale in `MODEL_NOTES.md` with before/after sample outputs.

Pre-emptively keep `_heal_input()` defensive coercion in producer nodes — same workaround as `account-research-agent` for Haiku's structured-output quirks. Costs nothing if Sonnet stays, saves a refactor if it doesn't.

## Output

Weekly email digest, **Saturday 7am local**, via Resend. One signal = one block with `why_this_matters` reasoning. Confidence exposed. No HTML decoration.

If no signals: digest still ships with "No new signals — system healthy." Silence is a failure mode.

## Watchlist contract

Flat `watchlist.yaml`. Required field: `domain`. Optional: `priority`, `mute_types`, `notes`.

**Not coupled to account-research-agent.** A forker can run with a list of domains alone. Coupling would have given richer "why this matters" reasoning but breaks the modular OSS framing.

## Runtime

- launchd local, Saturday 7am. v1 is single-machine.
- Cost cap: $4/run hard, $0.25/company soft.
- Kill switch: `.tmp/PAUSE` file, `bin/unload-launchd.sh`, auto-pause after 2 consecutive cap-hit runs.
- Logging: per-run dir at `.tmp/runs/<date>/` with API call log, raw candidates, critic verdicts, final digest.

## Eval discipline

- **Offline replay** at `evals/replay/`: ~30 frozen HTML snapshots, 5 per type, positive + 2 negatives per type. Snapshot-2 includes hallucination-bait (quote attributed to URL but URL doesn't contain it) to prove `evidence_traceable` clamp works. Runs on every commit.
- **Live-web** quarterly: 5–8 companies, manual verification, results in `EVAL_LOG.md`. Failures curated into `KNOWN_ISSUES.md`.
- README publishes both numbers per release.

## Build sequencing

The grilling session locked the design. The build sequence is:

- **Day 1 (this scaffold):** repo skeleton, Pydantic schemas for 6 types, state.py with `merge_signals` reducer, LangGraph state graph stub, config.yaml, watchlist.yaml seed, .env.example, pyproject.toml.
- **Day 2:** producer for `relevant_jd` (simplest — Tavily), critic stub, smoke-test end-to-end with `digest_compiler` printing to stdout.
- **Day 3:** remaining 5 producer types + Anthropic `web_search` fallback wiring.
- **Day 4:** `evidence_traceable` Python check + `SpendTracker` + `merge_signals` battle-test.
- **Day 5:** Resend wiring + launchd plist + offline replay scaffold + first manual e2e run.
- **Day 6 (Saturday):** first automated run hits inbox.

## What this is NOT

- Not a cold-email tool. The digest is intel for Anderson; it does not push to Instantly/Apollo.
- Not a "use AI on every signal" demo. The taxonomy + identity-key + recency windows are the architecture; LLM use is bounded to where reasoning is actually required.
- Not coupled to account-research-agent. The two tools compose; they don't depend.
- Not multi-tenant. v1 is single-user, single-machine. Productization is Phase B.

## See also

- `docs/architecture.md` — full state-machine walkthrough
- `EVAL_LOG.md` — quarterly live-web eval history
- `KNOWN_ISSUES.md` — honest curation disclosure
- `MODEL_NOTES.md` — Sonnet → Haiku downgrade rationale per node
