# Parity matrix — poc2 vs poc1 (Phase 5 exit artifact)

Every poc1 behavior, demonstrated in poc2, plus the four new capabilities.
"Verified" = observed on account 296497502276 during the phase's exit run
(receipts: the CLAUDE.md status table per phase, batch ids in each PR).

## poc1 behavior parity

| # | poc1 behavior | poc2 status | Evidence |
|---|---|---|---|
| 1 | Meridian 68/B; P1–P3 = CPO/CFO/IT Director; IT Director email pattern-guessed at HIGH confidence; TriNet incumbent; identified P3 survives verification into selection | ✅ | Phase 1 local Bedrock run + Phase 2 SFN batch + Phase 3 `--fixture-only` parity batch (`batch-p3-parity-001`) |
| 2 | Northwind 59/C; `firstinitial.lastname` MODERATE → P3 email withheld, fails verification; Rippling incumbent → warm paths 0; identified P1 reaches selection | ✅ | same runs as #1 |
| 3 | 10 artifacts/account (1 summary + 3 contacts × 3), §11.2 S3 key layout byte-identical | ✅ | Phase 1/2 runs; layout asserted in `tests/test_bdr_pipeline_offline.py` |
| 4 | Manifest with computed/cached counts, terminal state ARTIFACTS_QUEUED_REVIEW | ✅ | every run manifest |
| 5 | Write-once checkpoints; replay of a completed batch <1s/account, 0 model calls | ✅ | Phase 2: replay 0.8s/account, computed=0 cached=23 |
| 6 | Validation status decided IN CODE both directions; Haiku advisory reasons only | ✅ | `poc2/stages/validate.py`; offline tests |
| 7 | Email policy: direct always attaches; pattern guess only at HIGH confidence | ✅ | encoded from config (`email_policy: high_confidence_only`); golden + offline tests |
| 8 | HRIS prioritizer byte-identical (extraction, scorer hardening, P1–P3) | ✅ | `diff` clean vs poc1; 16 golden tests locked incl. 68/B fixture |
| 9 | Verify-then-reconcile; VERIFIED iff email OR phone; alphabetical select-3 | ✅ | strategies + offline tests; `priority_first` offered as config alternative |
| 10 | Exactly-5-bullet summary defended in code | ✅ | `poc2/stages/summary.py` |
| 11 | 9-Opus generation fan-out, per-artifact checkpoints, adaptive retries | ✅ | engine artifacts-axis fan-out; ported boto adaptive-retry config |
| 12 | SchemaModel null→[] coercion; PROMPT_EXCLUDE never serialized into prompts | ✅ | `poc2/models.py` ported verbatim |
| 13 | SFN Map envelope: MaxConcurrency 3, 50% tolerance, Lambda proxy, boto3 bundled | ✅ | Phase 2; ASL asserted in `tests/test_envelope.py` |
| 14 | Fixture/attached page fetching; Meridian + Northwind mocks with divergent paths | ✅ | ported verbatim; chain is config now |
| 15 | Idempotent, order-independent installer family; model-access preflight | ✅ | Phase 0/2; teardown + reinstall exercised live in Phase 2 |

## The four new capabilities (not in poc1)

| Capability | Status | Evidence |
|---|---|---|
| Declarative pipeline config — stages/tiers/prompts/policies/flow in versioned YAML; startup lints incl. the opus tier lint; behavior changes without code edits | ✅ | the engine itself; `identify_only.yaml`/`enrich_demo.yaml` reuse stages under a different flow; invoke-time `param_overrides` |
| AgentCore Browser fetch (Exa reversed) — live team pages + role-targeted LinkedIn SERPs + signals pass; replay never reopens a browser | ✅ | Phase 3: Basecamp identify lane end-to-end in AWS (`batch-p3-real-001`), replay computed=0 |
| AgentCore Memory — per-BDR voice + account event history | ✅ | Phase 4: same account, two bdr_ids → measurably different artifacts (9.1 vs 12.6 words/sentence, casual vs formal); events read back per batch |
| Gateway + Observability — internal MCP tools (mock CRM proving §4.3) + cost-per-stage traces | ✅ | Phase 5: `enrich_demo.yaml` fills missing firmographics through the gateway; `_trace.json` + `cost_report.py` table per run |

## Migration contract validation (MIGRATION §8)

`python -m deploy.config` resolves the full per-account resource set from
`config.json` alone; a foreign-account/second-stack dry run (prefix swapped to
`bdr-poc2b`, agent/memory names adjusted) re-derives every name with no other
edits — output reviewed in the Phase 5 PR. Bucket names stay globally unique
by construction (`{prefix}-artifacts-{accountId}`).

## Still deferred (unchanged non-goals)

HITL Gates 1 & 2, weekly scheduler, Bedrock Guardrails, full per-field
provenance, real CRM integration, enrichment auto-apply loop-back.
