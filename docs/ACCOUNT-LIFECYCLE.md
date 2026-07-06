# The life of an account — functional walkthrough

What actually happens to ONE account, end to end, using real data from the
verified runs on 296497502276 (Meridian Robotics, the 201-500 mock). Every
step below is declared in `pipelines/bdr_outreach.yaml`; the engine
(`poc2/pipeline/engine.py`) just interprets it.

## 0 · Entry — how an account arrives

```
scripts/run_batch.py --batch-id batch-X          (or the weekly scheduler, later)
  └─ Step Functions bdr-poc2-batch-envelope      Map over accounts, MaxConcurrency 3
       └─ Lambda bdr-poc2-invoke-account         SFN→AgentCore proxy
            └─ AgentCore Runtime                 one invocation = one account
                 payload: { account, batch_id, param_overrides }
```

The runtime loads the pipeline YAML, lints it (startup errors: duplicate ids,
dangling flow refs, missing prompt files, opus-without-generation, bad
fan_out), then walks the `flow`.

## 1 · Pre-flight (parallel): validate ∥ identify — ~7s

Two lanes run threaded.

**validate** (agent · haiku): Haiku is asked for advisory `reasons` only.
The status is computed IN CODE from required-field presence — poc1 learned
that Haiku hallucinates field presence in both directions.
→ checkpoint `validate`: `{status: VALID, missing_fields: []}` (~1.3s, ~1k tokens, $0.0013)

**identify** (composite) runs four children in order:

1. `fetch_pages` (tool) — walks the config chain `attached → browser → fixture`:
   - *attached*: the CRM payload carried `page_texts`? Use them. (Not for Meridian.)
   - *browser*: an AgentCore Browser session (aws.browser.v1, Playwright over
     CDP) runs the recipe — direct team paths (`/about`, `/team`, `/leadership`…),
     a DuckDuckGo discovery search, role-targeted `"<company>" "<role>" LinkedIn`
     searches (SERP text captured as evidence), and a funding/hiring signals
     query. For the REAL account (Basecamp) this returned 8 pages.
   - *fixture*: `mocks/pages/{domain}.json` (Meridian demo path).
   → checkpoint `fetch_pages`: raw `[{url, text}]` — **replay never re-fetches**.
2. `enrich` (agent · sonnet) — the ONE model call in identification: judges
   anchor quality (strong/moderate/weak), roles, `linkedin_found`, incumbent
   signals — from the fetched pages only; empty pages → empty result with NO
   model call. → checkpoint `enrich` (~4.8s, ~2.7k tokens, $0.014)
3. `prioritize` (policy) — the verbatim-ported `hris_contact_prioritizer`:
   heuristic extraction → 0–100 Contact Access score → deterministic P1–P3.
   Meridian: **68/B**, P1 Elena Voss (CPO, direct email), P2 Marcus Webb (CFO),
   P3 Divya Krishnan (IT Director) — her email composed from the
   HIGH-confidence `firstname.lastname` pattern (the encoded email policy:
   direct always attaches; pattern guesses only at high confidence). TriNet
   incumbent detected → warm paths zeroed. → checkpoint `prioritize` (11ms, $0)
4. `persist_identified` (tool, `checkpoint: false`) — writes
   `identified_contacts.json` to S3 immediately, so identification survives
   even if the account later fails the barrier.

## 2 · Verification fan-out — ~17s

Pool = 4 CRM contacts + 3 identified = 7. The engine fans `verify` over the
`contact_pool` items provider, one Haiku call each, threaded, each with its
own checkpoint `verify#<contact_id>`. Rule: VERIFIED iff valid email OR phone.

Meridian outcome: 6/7 verified — "Dead Lead" (junk email, junk phone) fails;
the pattern-guessed P3 **passes**. (~7 calls, ~8.6k tokens, $0.015)

## 3 · Reconcile + barrier — ~9ms

`reconcile` (policy · `alphabetical`, swappable to `priority_first` in config)
sorts the verified pool by last name and selects exactly 3:
**Alvarez, Berg, Krishnan** — the identified IT Director makes the cut.

Then the declarative barrier: `require: [account_valid, three_verified]`.
Both true → proceed. (An invalid account halts here; identification is
already persisted.)

## 4 · Research + summary — ~19s

- `research` (sonnet): company facts + trigger events, each origin-tagged
  `reasoning:no-live-tools` so provenance stays honest. → checkpoint (~$0.013)
- `summary` (sonnet): the exactly-5-bullet brief every generator shares —
  truncated to 5 in code, defended against model drift. → checkpoint (~$0.009)

## 5 · Generation fan-out — ~61s wall (threaded), the cost center

3 selected contacts × 3 artifact types = **9 Opus calls**, each its own
checkpoint `gen#<contact_id>#<artifact>`. Each prompt assembles: product
context + **BDR voice** + the 5 bullets + research + the contact record.

Voice comes from AgentCore Memory by the account's `bdr_id`
(`voice: memory`). Verified: the same account generated for `bdr-emea-07`
(casual seed) vs `bdr-na-04` (formal seed) produced measurably different
artifacts — 9.1 vs 12.6 words/sentence, "— sam" vs "Kind regards, Jordan
Ellis". No exemplars / local run → static snippet fallback, automatically.

Cost: ~$0.27 of the account's ~$0.32 total — which is why every one of these
is individually checkpointed.

## 6 · Persist — terminal state

`persist` (tool, `checkpoint: false` — S3 writes are idempotent by key and
must re-run on replay) writes the poc1 §11.2 layout:

```
{batch_id}/{bdr_id}/{account_id}/
    identified_contacts.json      (written back in step 1)
    account_summary.json
    contacts/{contact_id}/{email,linkedin,talk_track}.json   × 3 contacts
    _manifest.json                (status, selection, idempotency counts)
    _trace.json                   (per-stage tokens/latency/cost)
```

It also appends the account's event to Memory
(`"batch X ran … 10 artifacts queued … access 68/B …"`) and returns the
manifest — terminal state `ARTIFACTS_QUEUED_REVIEW`.

## 7 · The second life: replay

Re-running the SAME `batch_id` is the operational superpower. Every
checkpoint above lives in DynamoDB at `(batch_id, account_id, stage_key)`,
write-once. On replay the engine finds all 23 stored outputs:

| | cold | replay |
|---|---|---|
| wall clock / account | ~36s | **0.8s** |
| model calls | 12 (1+1+7+1+1... plus 9 gen = 20) | **0** |
| browser sessions | 0–1 | **0** |
| cost | ~$0.32 | ~$0 |
| S3 artifacts | written | re-written (idempotent, self-heals a wiped bucket) |

A failed batch re-run therefore only pays for the accounts/stages that never
completed — the 50%-tolerance Map envelope plus write-once checkpoints make
retries free where work already succeeded.

## Cost of one cold account (verified trace, batch-p5-cost-001)

| stage | tier | calls | ms | tokens in/out | USD |
|---|---|---|---|---|---|
| fetch_pages | — | 1 | 92 | 0/0 | 0 |
| validate | haiku | 1 | 1,314 | 1,016/55 | 0.0013 |
| enrich | sonnet | 1 | 4,830 | 2,231/505 | 0.0143 |
| prioritize | — | 1 | 11 | 0/0 | 0 |
| verify | haiku | 7 | 17,512 | 6,948/1,637 | 0.0151 |
| reconcile | — | 1 | 9 | 0/0 | 0 |
| research | sonnet | 1 | 12,288 | 1,092/632 | 0.0128 |
| summary | sonnet | 1 | 6,700 | 1,452/332 | 0.0093 |
| generate | opus | 9 | 61,349 | 11,418/1,270 | **0.2665** |
| persist | — | 1 | 4,442 | 0/0 | 0 |
| **TOTAL** | | 25 | ~109s cpu / ~36s wall | 24,157/4,431 | **$0.3193** |
