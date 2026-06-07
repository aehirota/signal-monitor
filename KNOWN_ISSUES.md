# Known issues

Honest curation disclosure. Populated as v1 ships and the live-web eval
runs surface real failure modes. Same role as account-research-agent's
KNOWN_ISSUES.md: documenting what doesn't work is the architect's signal
that they are paying attention to what does.

## v1.0 (first live run, 2026-06-06)

### False negatives from the `evidence_traceable` clamp

The clamp fetches `source_url` with httpx + BeautifulSoup and substring-matches
`evidence_quote`. This catches the **important** failure mode (hallucinated
quotes attributed to real URLs) but produces **predictable false negatives**
against three classes of pages:

1. **JS-rendered SPA job boards** (builtinnyc.com, lever.co page shells,
   workday): the rendered HTML is a skeleton; the JD content is fetched and
   composed client-side. httpx sees an empty shell → score 0.
2. **Anti-bot-protected pages**: Cloudflare-protected sites return 403 or a
   captcha shell → score 0.
3. **Paywall / login walls**: short stub content shown to anonymous
   fetchers → score 0.

**Observed in live run 2026-06-06 against retool.com:** Tavily surfaced two
genuine, ICP-relevant JDs ("Retool GTM Engineer" + "Retool Manager, Revenue
Strategy & Operations") with verbatim quotes from the Tavily search snippet.
The clamp rejected both because builtinnyc.com is JS-rendered. The signals
ARE preserved in `.tmp/runs/<id>/run.json` under `failed_signals` with full
critic verdicts.

**The v1 trade-off is intentional:** false negatives are recoverable from the
audit log; false positives (hallucinated quotes shipped to the digest) would
break the digest's trust property and can't be recovered. We choose
recoverable-false-negative over unrecoverable-false-positive.

**Planned v1.1:** surface clamp-parked signals in a separate "Pending
verification" section of the digest — preserves the architectural
commitment ("only verified signals are surfaced as such") while not losing
the gold for human review.

### Anthropic Tier 1 rate limits

Live run hit the 30k Sonnet / 50k Haiku tokens/min ceiling during the
parallel producer super-step. Fixed for v1.0 by:
1. Downgrading producer + orchestrator models to Haiku 4.5
2. Enabling Anthropic SDK `max_retries=5` exponential backoff
3. Capping Exa per-result content at 2000 chars (was full article text,
   blew through 200k input window on `tech_signal` queries)
4. Capping Perplexity result expansion (was attaching full answer text to
   every citation, creating 10× duplication)

Run cost dropped to ~$0.08/domain after these fixes (was ~$0.12 + rate-limit
failures on first attempts).

### Intermittent "state missing config" on parallel producers

In ~1 of every 3 runs, one or two parallel producers see empty `state["config"]`
on entry — root cause not fully diagnosed (suspected LangGraph parallel
super-step ordering edge case). Worked around with a disk re-load fallback
in `_produce_for_type` (config is static per-run, safe to re-read).
Investigate further if it becomes more frequent.

## Pre-emptive flags (carry-overs from account-research-agent)

These are known LLM behaviors observed elsewhere; we are coding defensively
for them on day 1 so we don't discover them mid-build.

- **Haiku structured-output quirk**: occasionally emits list-typed fields
  as JSON-encoded strings. `_heal_input()` defensive coercion in producer
  nodes covers this — same workaround as account-research-agent. Currently
  irrelevant (v1.0 ships Sonnet everywhere) but stays in the code so the
  Sonnet→Haiku downgrade ratchet doesn't re-trip it.

- **Search source decay**: Exa / Tavily / Perplexity coverage shifts over
  time. Live-web eval is quarterly specifically to catch this. Any signal
  type that drops below ~60% offline-replay pass rate gets flagged here.

- **Evidence quote mismatch from JS-rendered pages**: some SPA pages return
  empty shells to non-browser fetchers. `evidence_check` treats unverifiable
  as failed (score 0). This is intentional but will cause some legitimate
  signals to fail the clamp. Flag in this file with examples once observed.
