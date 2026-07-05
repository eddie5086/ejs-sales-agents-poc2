# Implementation plan — six phases, one PR each

Product target: mirror poc1's behavior (see `../ejs-sales-agents-poc/docs/WORKFLOW.md`
for the authoritative per-stage description) while adding the two poc2 axes:
declarative configurability and AgentCore-native services.

Every phase: build → verify against the exit criterion → update CLAUDE.md
status table → PR. Any AWS resource introduced in a phase extends
`scripts/install.py` and `deploy/config.py` **in the same PR** (see MIGRATION.md).

---

## Phase 0 — Scaffold + engine skeleton + migration tooling

**Scope**
- Repo layout: `poc2/` (package), `poc2/pipeline/` (schema + engine),
  `pipelines/` (YAML configs), `prompts/` (externalized prompt files),
  `deploy/`, `scripts/`, `tests/`, `mocks/`.
- `config.json` + `deploy/config.py`: the single source of truth pattern from
  poc1 (port `../ejs-sales-agents-poc/deploy/config.py` and adapt names).
  Account id via STS; every resource name derived from `resource_prefix`.
- Pipeline config schema (pydantic): stages with
  `{id, kind: policy|agent|tool|composite, tier?, strategy, prompt?, params,
  generation?: bool}` plus a `flow` list of stage ids and `{parallel: [...]}`
  groups. Validators: unique ids, flow references exist, prompt files exist,
  **tier lint** (opus requires `generation: true`).
- Engine: interprets the config; per-stage strategy resolution from a registry;
  each stage checkpointed write-once (port poc1 `poc/state.py` StateStore —
  in-memory default, DynamoDB backend activates in Phase 2); parallel groups
  via ThreadPoolExecutor. Ship 2–3 trivial policy strategies + `pipelines/demo.yaml`.
- `scripts/install.py` (validates config, prints STS identity, preflights
  Bedrock model access, reports "no resources yet"), `scripts/uninstall.py`
  (enumerates derived resources, deletes-if-exists), `MIGRATION.md` kept true.
- `pytest.ini` with `testpaths = tests` from day one (poc1 got bitten by stray
  same-named test files breaking collection).

**Exit criteria**: `pytest -q` green (schema validation, DAG/flow validation,
write-once checkpointing, parallel group execution); `python -m poc2.run
pipelines/demo.yaml` executes from config; `python -m deploy.config` prints a
correct resolved resource set.

## Phase 1 — Port the product behind the engine

**Scope**
- Port domain models, `ArtifactStore` (S3/local, poc1 §11.2 key layout kept
  identical), and every poc1 stage as a registry strategy:
  validation (deterministic required-fields + Haiku reasons), identification
  composite (`fetch_pages → enrich → prioritize`), verification (email OR
  phone), reconciliation (alphabetical select-3; strategy-swappable), research
  (Sonnet), summary (exactly-5 bullets, Sonnet), generators (email/linkedin/
  talk_track, Opus ×9 fan-out — `fan_out: per_contact` in config).
- Port `hris_contact_prioritizer.py` **verbatim** + its 16 golden tests +
  poc1's integration tests + `mocks/` (sample account, sample batch, both
  fixture pages). Encode the email-confidence policy in the identify strategy.
- Prompts move to `prompts/*.md`, referenced from the pipeline YAML.
- `pipelines/bdr_outreach.yaml`: the full poc1-equivalent pipeline.
- Strands agent factory (port `poc/bedrock.py` — tier → model + adaptive
  retries) unless the engine's agent-stage wrapper makes it thinner.

**Exit criteria**: all ported + new tests green offline; a local run against
real Bedrock on the two mock accounts reproduces poc1's verified outcomes —
Meridian **68/B** with the IT Director's email pattern-guessed at high
confidence, Northwind **59/C** with the P3 email withheld at moderate
confidence — driven entirely by `pipelines/bdr_outreach.yaml`.

## Phase 2 — Deploy the envelope

**Scope**: AgentCore Runtime entrypoint (deferred heavy imports — 30s init
cap), container deploy via CodeBuild, execution-role policies (S3/ECR/
DynamoDB), DynamoDB state table, Lambda SFN→AgentCore proxy, Step Functions
Map batch envelope (MaxConcurrency 3, 50% failure tolerance). Port poc1's
`scripts/deploy_*.py` + `install.py` orchestration wholesale; rename via
`deploy/config.py`. Read `docs/AWS-GOTCHAS.md` first.

**Exit criteria**: `scripts/install.py` deploys the whole stack to the config'd
account; a 2-account SFN batch runs cold (~22 stages/account) and replays the
same batch_id at <1s/account fully cached; teardown via `uninstall.py` works.

## Phase 3 — Live web fetch via AgentCore Browser tool

**Scope**: a `browser` fetch strategy for the identify composite — an AgentCore
Browser session searches the web for the account's team/leadership pages,
fetches them, and runs the role-targeted searches poc1 left TODO
(`"<company> <role> LinkedIn"`, signals pass). Raw fetched text checkpoints
write-once (replay never reopens a browser). Fallback chain in config:
`[attached, browser, fixture]`. Installer grows Browser-tool permissions.

**Exit criteria**: with fixtures deleted, one **real** company account (mocks
are fictional) completes the identify lane via Browser end-to-end in AWS;
fixture path still passes offline tests; replay does not open browser sessions.

## Phase 4 — AgentCore Memory

**Scope**: a Memory store (name derived from prefix; installer grows it) holding
per-BDR voice exemplars + per-account event history. Seed script for two
distinct BDR voices. Generator stages retrieve voice by `bdr_id` when the
pipeline sets `voice: memory` (static snippet remains the local fallback).
Batch runs append account events (batch ran, artifacts queued).

**Exit criteria**: same account, two different `bdr_id`s → measurably different
artifact voice, from config+memory only; local runs still work with
`voice: static`.

## Phase 5 — Gateway + Observability + parity docs

**Scope**: AgentCore Gateway exposing internal tools as MCP (first target: a
mock CRM-lookup Lambda proving poc1's deferred §4.3 enrichment contract);
per-stage token/latency/cost traces via AgentCore Observability (poc1 had only
raw X-Ray); final docs — WORKFLOW.md for poc2, MIGRATION.md validation on a
clean-account dry run (`python -m deploy.config` for a foreign account), and a
**parity matrix** vs poc1 (every poc1 behavior demonstrated in poc2 + the four
new capabilities).

**Exit criteria**: parity matrix all green; enrichment tool callable through
Gateway from a pipeline stage; a cost-per-stage table producible from traces.

---

## Non-goals (unchanged from poc1 — do not build)

HITL Gates 1 & 2, weekly scheduler, Bedrock Guardrails, full per-field
provenance, real CRM integration. They remain the roadmap AFTER parity.
