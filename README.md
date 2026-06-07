# Signal Monitor

A LangGraph agent that watches a list of companies for dated buying-window signals and emails you a weekly digest. Designed as a companion to [account-research-agent](https://github.com/aehirota/account-research-agent), but runs standalone.

**Status:** v1.0 shipped (2026-06-06) — production-ready, MIT, in active use.

## First live run

Five-company watchlist (Vercel, Retool, Anthropic, Clay, Apollo), first real run:

```
Subject: Signal Monitor — 6 new signals across 3 companies

VERCEL.COM  [relevant_jd]
Open req: GTM Engineer (US Remote)  (confidence: 0.95)
URL: https://vercel.com/careers/gtm-engineer-5777645004
Why this matters: Open GTM Engineer req at Vercel = internal gap
acknowledged. Position yourself as the ready-now architect before
a 9-month hire fills it.

[... 5 more signals ...]

Watchlist: 5 companies | Signals this week: 6 | Run cost: $0.56 / $4.00 cap
```

Six action-grade signals, every quote verified against its live URL by code, $0.56 spend, 90 seconds wall-clock.

## What it does

You give it a YAML list of company domains. Every Saturday at 7am, the agent:

1. Scans the web for **six typed signals** per company over the last week
2. Critiques every candidate signal for evidence traceability, recency, type-match, and actionability
3. Deduplicates against everything it has ever surfaced (signal-identity keys, not URL hashes)
4. Emails you a digest of what survived, with reasoning per signal

If nothing fires, it tells you so. If something fires, the digest tells you *why it matters*.

## The six signal types (v1)

| Type | What it is | Why it's a buying window | Recency window |
|---|---|---|---|
| `funding_round` | Series A/B/C/D announcement | Cash-in → tooling spend window opens within 60-90 days | 120 days |
| `exec_move` | New CRO / COO / VP Sales / Head of RevOps / Head of GTM Eng | New leader has 90-day mandate; rebuilds stack | 90 days |
| `relevant_jd` | Open req for GTM Eng / RevOps Architect / AI SDR / similar | They're admitting they don't have the capability internally | 45 days |
| `product_launch` | New product, GA announcement | Growth pressure → outbound + RevOps re-tooling | 60 days |
| `tech_signal` | Public mention of stack migration or evaluation (HubSpot / Salesforce / Apollo / Clay / n8n) | They're touching the stack right now | 30 days |
| `news_strategic` | M&A, IPO file, layoffs, reorg | Strategic shift → priorities and tooling realign | 60 days |

A signal that doesn't fit a type gets dropped. The taxonomy is the contract.

## Architecture

LangGraph state machine. Anthropic SDK direct for leaf nodes (no LangChain wrappers).

```
ENTRY
  ↓
[loader]                       # load watchlist + dedup store + recency windows
  ↓
[producer_per_type] × 6        # parallel super-step, one node per signal type
  ↓                            # each queries its primary source
  ↓ (merge_signals reducer)
[critic]                       # per-signal critique with code-enforced evidence check
  ↓
  ├─ all pass → [deduper]
  └─ some fail → [orchestrator]
                    ↓
                    Send to failed producer with fallback hint (max 1 retry)
                    ↓
                    [critic] (second pass)
                    ↓
                    [deduper]
  ↓
[digest_compiler]              # produces final email payload
  ↓
EXIT
```

### Source assignment (per signal type)

| Signal type | Primary | Fallback (if empty / low-confidence) |
|---|---|---|
| `funding_round` | Exa | Anthropic `web_search` |
| `exec_move` | Exa | Anthropic `web_search` |
| `relevant_jd` | Tavily | Anthropic `web_search` |
| `product_launch` | Perplexity | Anthropic `web_search` |
| `tech_signal` | Exa | Anthropic `web_search` |
| `news_strategic` | Perplexity | Anthropic `web_search` |

### Architect-level decisions

1. **Typed signal taxonomy as the contract.** Anything that doesn't fit one of the six types gets dropped. The taxonomy enforces precision over recall.
2. **Identity-key dedup, not URL dedup.** Each signal type defines what makes it unique (e.g. `funding_round` → `(company, round_type, year_month)`). Same Series B reported across five outlets = one signal identity.
3. **Per-type recency windows.** A signal expires from the digest when it exits its window — different per type. Stays in the audit log forever; never re-surfaces.
4. **Code-enforced evidence traceability.** The critic checks `evidence_traceable` by fetching the URL and grepping for the quoted snippet, not by asking the LLM. Hallucinated quotes attributed to real URLs can't pass. Same clamp pattern as the disqualifier policy in `account-research-agent`.
5. **Critic-driven re-fire via LangGraph `Send`.** When a signal fails the critic, the orchestrator dispatches selectively to the failed type's producer with a "try the fallback source" hint. Self-correcting, not one-shot.
6. **Per-node model assignment with empirical ratchet.** v1.0 ships Sonnet 4.6 everywhere for safety; nodes downgrade to Haiku 4.5 only after measured stability. Documented in `MODEL_NOTES.md`.
7. **Cost cap enforced in code, not in prompt.** A `SpendTracker` wraps every API call; once the per-run cap is hit, further calls return `BudgetExceeded`. Same code-over-prompt enforcement as the disqualifier clamp.

## Output

One email per week, Saturday 7am, via Resend. One signal = one block, with the agent's `why_this_matters` reasoning per signal. No HTML decoration.

```
Subject: Signal Monitor — 3 new signals across 2 companies (2026-06-13)

VERCEL  [exec_move]
COO Jane Grosser published Nov-2025 thesis on the GTM Engineer role she's
hiring (signal_date: 2025-11-14, confidence: 0.92)
URL: https://...
Why this matters: New COO + public hiring thesis = active build phase.
Hook: tie outreach to her stated framework for the role.

—
Watchlist: 32 companies | Signals stored: 147 | Run cost: $1.62 / $4.00 cap
Pause: touch ~/Signal-Monitor/.tmp/PAUSE
```

If no signals fired: the digest still ships with "No new signals across N companies — system healthy" so you know it ran.

## Cost ceiling

- Per-run hard cap: **$4.00** (code-enforced)
- Per-company soft cap: $0.25 (skip remaining types if exceeded)
- Expected weekly run: ~$1.50–$2.00 at 30 companies
- Expected monthly: **~$8–$12**

## Eval

Hybrid: offline replay (deterministic, every commit) + live-web (quarterly, logged).

- `tests/replay/snapshots/` holds frozen HTML + expected outputs per signal type. Includes hallucination-bait negatives that prove the `evidence_traceable` clamp clamps.
- Live-web eval runs quarterly against 5–8 companies with known recent signals. Results logged to [`EVAL_LOG.md`](EVAL_LOG.md). Failures get curated into [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

Public quality bar in this README updates per release.

## Quickstart

```bash
git clone https://github.com/aehirota/signal-monitor.git
cd signal-monitor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with: ANTHROPIC_API_KEY, EXA_API_KEY, TAVILY_API_KEY,
# PERPLEXITY_API_KEY, RESEND_API_KEY, DIGEST_TO_EMAIL

# Edit watchlist.yaml with the companies you want to watch
python run.py
```

A first run hits the configured email. See [`docs/architecture.md`](docs/architecture.md) for the full state-machine walkthrough.

## Companion to Account Research Agent

[account-research-agent](https://github.com/aehirota/account-research-agent) decides **who fits and why** (one-time per company).
**Signal Monitor decides when to act on the fit you already validated** (weekly per company).

Together they form a two-stage GTM pre-outbound layer:

- Run Account Research Agent against a target domain → get an ICP-fit brief
- If `recommendation = Pursue`, add the domain to Signal Monitor's `watchlist.yaml`
- Every Saturday you see what's happening at the companies you decided to pursue

The two tools are **modular, not coupled** — Signal Monitor works without Account Research Agent. The composition is the architecture; the coupling is optional.

## License

MIT. See `LICENSE`.
