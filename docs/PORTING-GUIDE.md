# Porting guide — what to take from poc1, and how

poc1 = `../ejs-sales-agents-poc` (also `github.com/eddie5086/ejs-sales-agents-poc`,
master). It is deployed and verified; treat its behavior as the spec.

## Port VERBATIM (proven code — do not "improve")

| poc1 path | poc2 destination | Notes |
|---|---|---|
| `poc/agents/hris_contact_prioritizer.py` | `poc2/lib/hris_contact_prioritizer.py` | THE handed-over 3-contacts engine (extraction + 0–100 Contact Access score + deterministic P1–P3). Stdlib-only, zero time/randomness. Its input-hardening (`_tokens`/`_email_pattern`/`_warm_path_type`) fixed real production drift — keep byte-identical. |
| `tests/test_hris_contact_prioritizer.py` | `tests/` | 16 golden tests incl. the locked 68/B fixture. Only the import line changes. |
| `mocks/sample_account.json`, `mocks/sample_batch.json`, `mocks/pages/*.json` | `mocks/` | Two DISTINCT companies: Meridian (201-500, high-conf email pattern, TriNet) and Northwind (51-200, moderate pattern → P3 email withheld, Rippling). They exercise divergent paths — keep both. |
| `poc/state.py` (StateStore) | `poc2/state.py` | Write-once checkpoint semantics + DynamoDB/in-memory backends. Engine calls it with config-derived stage ids. |
| `poc/storage.py` (ArtifactStore) | `poc2/storage.py` | S3/local with identical key layout (§11.2 of poc1's handoff). |
| `lambda/invoke_account/handler.py` | `lambda/invoke_account/` | SFN→AgentCore proxy. |
| `poc/orchestration/batch_envelope.asl.json` | `poc2/orchestration/` | Deployed Map state machine. Rename resources via deploy config. |
| `scripts/deploy_agentcore.py`, `deploy_dynamodb.py`, `deploy_stepfunctions.py`, `install.py` | `scripts/` | The installer family. Adapt names through `deploy/config.py`; keep the idempotent, order-independent property. |
| `deploy/config.py` | `deploy/config.py` | Single-source-of-truth resource derivation. Extend with poc2 resources (memory store, gateway) as phases add them. |

## Port AS STRATEGY IMPLEMENTATIONS (logic identical, shape changes)

Each poc1 agent module becomes a registry strategy the engine resolves from
config; prompts move to `prompts/*.md`:

- `account_validation.py` — REQUIRED-FIELD CHECK STAYS IN CODE; the Haiku call
  only supplies advisory `reasons` (poc1 lesson: the model hallucinated field
  presence in both directions).
- `contact_identification.py` — becomes the `identify` composite's `prioritize`
  child. Keep the **email policy**: `email_direct` always attaches; pattern
  guesses attach ONLY at high pattern confidence.
- `contact_verification.py` — VERIFIED iff valid email OR phone.
- `contact_reconciliation.py` — alphabetical select-3 (make the strategy name a
  config value; `priority_first` is an obvious second strategy to offer).
- `contact_enrichment.py` — the Sonnet judgment agent (anchors/triggers/
  incumbents from fetched pages ONLY, never fabricates, empty pages → no model
  call). Its system prompt moves to `prompts/enrich.md`.
- `research.py`, `account_summary.py` (exactly-5 bullets), `generators.py`
  (email <120 words one hook one ask; LinkedIn <300 chars; talk track + voicemail).
- `bedrock.py` — tier → BedrockModel factory with ADAPTIVE RETRIES (the 9-Opus
  fan-out throttles Bedrock under concurrent accounts without it) and tier
  temperatures (haiku 0.0 / sonnet 0.3 / opus 0.7).
- `models.py` — pydantic domain models. KEEP `SchemaModel` (coerces LLM `null`
  → `[]` for list fields; real production bug fix) and `PROMPT_EXCLUDE`
  (contacts/page_texts never serialized into prompts).

## Do NOT port (rebuilt or reversed in poc2)

- `poc/orchestration/runner.py` — replaced by the config-interpreting engine.
- `poc/agents/page_fetcher.py`'s **Exa client** — poc2 uses the AgentCore
  Browser tool instead (user decision). Keep only the fixture + attached-pages
  logic as fetch strategies.
- The static BDR voice snippet in `generators.py` — becomes the `static`
  fallback behind AgentCore Memory.
- `EXA_API_KEY` / `PAGES_FIXTURE_DIR` env plumbing — fetch chain is pipeline
  config now.

## Behavior parity targets (verify in Phase 1)

From poc1's verified runs (its CLAUDE.md status table is the receipt):
- Meridian Robotics (201-500): access **68/B**, P1–P3 = CPO / CFO / IT Director,
  IT Director's email composed from a HIGH-confidence `firstname.lastname`
  pattern, TriNet incumbent, identified P3 survives verification.
- Northwind Logistics (51-200): access **59/C**, `firstinitial.lastname` at
  MODERATE confidence → P3 (Head of IT) gets NO email and fails verification;
  Rippling incumbent → warm paths 0; identified P1 (HR Director, direct email)
  reaches the final selection.
- Full account: 10 artifacts (1 summary + 3 contacts × 3), manifest with
  `computed`/`cached` counts; replay of a completed batch <1s/account, 0 model calls.
