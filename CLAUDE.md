# CLAUDE.md — start here

**What this is.** The handoff bundle for **ejs-sales-agents-poc2**: a
config-driven, AgentCore-native rebuild of the BambooHR BDR outreach
artifact-generation workflow. **No code exists yet** — this repo currently
contains the complete, reviewed plan for building it. Your job (the agent
session reading this) is to implement it, phase by phase.

**Read in this order:**

1. This file — orientation, locked decisions, conventions, status.
2. `docs/IMPLEMENTATION-PLAN.md` — the six phases, each with scope, exit
   criteria, and verification. Build them in order, one PR per phase.
3. `docs/ARCHITECTURE.md` — target architecture: the pipeline engine, the
   declarative config schema, and how each AgentCore service is used.
4. `docs/PORTING-GUIDE.md` — exactly what to port from the predecessor repo
   (`../ejs-sales-agents-poc`) verbatim vs. rebuild, with file paths.
5. `docs/AWS-GOTCHAS.md` — hard-won deployment and robustness knowledge from
   poc1. Read BEFORE any AWS deploy work; every item cost real debugging time.
6. `MIGRATION.md` — the any-account installability contract the installer must
   honor from Phase 0 onward.

**Reference projects (two, both consultable):**

- **poc1** — `ejs-sales-agents-poc`, at `../ejs-sales-agents-poc` and on GitHub
  (`eddie5086/ejs-sales-agents-poc`). **Working and deployed**: read its
  `CLAUDE.md` and `docs/WORKFLOW.md` for the product behavior poc2 must mirror.
  poc1 stays untouched — poc2 is a fresh scaffold that ports proven pieces
  selectively (see `docs/PORTING-GUIDE.md`).
- **BDRAWSRESEARCHTOOL** — a similar, earlier AgentCore harness implementation,
  at `../BdrAwsAgentClaude03Ed` and on GitHub
  (`eddie5086/BdrAwsAgentClaude03Ed`; the tool also mirrored as
  `eddie5086/BDRAWSRESEARCHTOOL`). Two reasons to consult it: (1) it is the
  ORIGIN of the HRIS 3-contacts logic poc1 ported (`workbench/skills/contacts.md`,
  `workbench/lib/hris_contact_finder.py`, `workbench/lib/hris_committee_scorer.py`,
  `workbench/config/company.yaml`, mirrored in `lambda_services/`) — useful for
  provenance and for extending the recipe (role-targeted searches, cadence);
  (2) it is a prior AgentCore harness build — compare its harness/deploy choices
  when making poc2's AgentCore-native decisions. For CODE, always port from
  poc1's copies (they carry fixes: per-line extraction, scorer hardening,
  golden tests), not from here.

---

## Current status (2026-07-05)

| Phase | State | Exit criterion |
|---|---|---|
| 0 — Scaffold + engine skeleton + migration tooling | ✅ done (2026-07-05) | 2-stage demo pipeline runs locally from config; `python -m deploy.config` prints resolved names — verified: 35 offline tests green, demo runs, resolved set correct on 296497502276, installer preflight passes |
| 1 — Port the product behind the engine | ❌ | local run vs real Bedrock reproduces poc1 output (Meridian 68/B, Northwind 59/C) from config alone |
| 2 — Deploy the envelope (Runtime + DynamoDB + SFN) | ❌ | AWS batch run + sub-second idempotent replay |
| 3 — Live web fetch via AgentCore Browser tool | ❌ | identify lane fetches via Browser for a real company; fixtures still work |
| 4 — AgentCore Memory (BDR voice + account history) | ❌ | two BDRs get distinguishably different voice from config+memory only |
| 5 — Gateway (internal MCP tools) + Observability + docs | ❌ | parity matrix vs poc1 all green + new capabilities demonstrated |

## Decisions locked (2026-07-05 — do not relitigate)

- **AgentCore-native platform**: Runtime + Browser tool + Memory + Gateway +
  Observability. Not just Runtime-as-compute like poc1.
- **Declarative pipeline config**: one versioned YAML defines every stage
  (enabled / strategy / model tier / prompt file / params). The runner is an
  engine that interprets it. Registries under the hood; no behavior changes
  require code edits.
- **AWS-native web search**: the **AgentCore Browser tool** replaces Exa (a
  user decision — poc1 chose Exa; poc2 explicitly reverses this). No
  third-party search API, no `EXA_API_KEY` anywhere. Fetch fallback chain:
  `attached page_texts → browser → local fixture`.
- **Migration tooling in parallel**: any PR that introduces an AWS resource
  must extend `scripts/install.py` + `deploy/config.py` in the same PR. All
  S3 buckets (and every other resource name) are config-defined or auto-derived
  per account. See `MIGRATION.md`.
- **Fresh scaffold, port selectively** — see `docs/PORTING-GUIDE.md`.
- **Business decisions carried over from poc1** (already user-approved; encode,
  don't re-ask): 3 contacts per account via the HRIS buying-committee logic
  (port `hris_contact_prioritizer.py` VERBATIM); pattern-guessed emails attach
  only at high pattern confidence; structured anchors only, generators own all
  copy; warm-path scoring degrades to 0 without a connections source; P1–P3
  ranking fully deterministic; anchor-quality judgment is the one model call in
  identification.

## Conventions (inherited from poc1, now engine-enforced)

- **Every stage checkpoints write-once** at `(batch_id, account_id, stage_id)`;
  replay is a no-op. New expensive stages must be checkpointed. Stage ids come
  from the pipeline config.
- **Objective checks belong in code, not the model** (poc1 learned this the
  hard way — Haiku hallucinated field presence).
- **Tier discipline**: Opus never creeps into non-generation stages. The config
  validator must *lint* this (an `opus` tier on a stage not flagged
  `generation: true` is a config error).
- **Deterministic modules stay deterministic**: no time/randomness/network in
  anything checkpointed as pure logic (replay must be byte-identical).
- **One PR per phase**, verified against the phase's exit criterion before
  merge. Docs (this file's status table above all) updated in the same PR.
- **Local-first**: the engine runs locally (in-memory state, local artifact
  dir, fixture fetch, static voice) with zero AWS resources; config selects
  the AWS-backed implementations.

## Initial resource identity (config.json)

Same AWS account/region as poc1 for now (296497502276 / us-east-1), fully
migratable via config: `resource_prefix: bdr-poc2`,
`agent_name: bdr_poc2_account_runner`, `artifact_bucket: ""` (auto-derives
`bdr-poc2-artifacts-{accountId}`). Model tiers: same IDs as poc1 — and the same
access blocks apply, see `docs/AWS-GOTCHAS.md` §1.

## Phase 0 notes — smallest-call decisions where the docs were silent

- **Composite children are top-level declared stages** referenced by id in the
  composite's `stages:` list (they need not appear in `flow`). Children
  checkpoint under their own ids; the composite itself is not re-checkpointed —
  its output is the mapping of child outputs.
- **Fannable kinds are `agent` and `tool`**; `fan_out` on `policy`/`composite`
  is a config error. Fan-out items come from the run payload
  (`per_contact` → `payload["contacts"]`), item checkpoint key
  `{stage_id}#{item_id}`.
- **Agent stages require a `tier`** (config error otherwise).
- **Barrier `require` names resolve in a condition registry**
  (`register_condition(name)` — predicates over accumulated stage outputs);
  an unsatisfied barrier raises and halts the account's pipeline.
- **`deploy/config.py` grew a static `validate()`** (prefix lowercase,
  agent_name underscores, region-family ↔ model-ID-prefix coupling per
  MIGRATION §7); `install.py` runs it before touching AWS.
- boto3/yaml imports in `poc2/state.py` and `deploy/config.py` are deferred
  into the code paths that need them, so offline runs/tests import zero AWS.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
pytest -q                                  # offline tests (pin testpaths=tests in pytest.ini from day one)
python -m poc2.run pipelines/demo.yaml     # run a pipeline locally
python -m deploy.config                    # print resolved per-account resources
python scripts/install.py                  # preflight + deploy everything that exists so far
python scripts/uninstall.py                # scripted teardown
```
