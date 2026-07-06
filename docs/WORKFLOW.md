# poc2 workflow — what runs, per account

Everything below is declared in `pipelines/bdr_outreach.yaml` and interpreted
by the engine (`poc2/pipeline/engine.py`). No stage order, model tier, prompt,
or policy lives in code — swapping any of them is a config edit.

## The flow

```
{parallel: [validate, identify]}          pre-flight, threaded
verify            (fan-out per contact over CRM + identified pool)
reconcile         (policy select-3; alphabetical default)
{barrier: require [account_valid, three_verified]}
research
summary           (exactly 5 bullets, defended in code)
generate          (fan-out contacts × [email, linkedin, talk_track] = 9 Opus)
persist           (checkpoint: false — idempotent writes run every replay)
```

## Stage by stage

| Stage | Kind/tier | What happens | Objective checks in code |
|---|---|---|---|
| validate | agent · haiku | Haiku writes advisory `reasons` only | required-field presence decides status, both directions |
| identify | composite | `fetch_pages → enrich → prioritize → persist_identified` | — |
| · fetch_pages | tool | chain `attached → browser → fixture`; browser = AgentCore Browser session running the contacts.md recipe (team pages + role-targeted LinkedIn SERPs + signals) | raw text checkpoints write-once; replay never re-fetches |
| · enrich | agent · sonnet | the ONE model call in identification: anchor quality, roles, linkedin_found — from fetched pages only | empty pages → empty result, no model call |
| · prioritize | policy | verbatim `hris_contact_prioritizer` (extraction, 0–100 access score, deterministic P1–P3) | email policy: direct always; pattern-guess only at HIGH confidence |
| verify | agent · haiku | VERIFIED iff valid email OR phone; per-contact checkpoint `verify#id` | contact_id echoed from code, not the model |
| reconcile | policy | select 3 from the verified pool (`alphabetical` \| `priority_first`) | raises if <3 verified |
| barrier | flow | declarative guards `account_valid`, `three_verified` | condition registry, no model |
| research | agent · sonnet | company facts + trigger events, origin-tagged | — |
| summary | agent · sonnet | the 5-bullet brief all generators share | bullets truncated to exactly N |
| generate | agent · opus | 3 contacts × 3 artifact types; voice from AgentCore Memory by `bdr_id` (`voice: memory`), static snippet fallback | per-item checkpoint `gen#contact#artifact` |
| persist | tool | §11.2 S3 layout (identical to poc1) + manifest; appends the account event to Memory | writes idempotent by key, never checkpointed |

## Invariants

- Every checkpoint is write-once at `(batch_id, account_id, stage_key)`;
  re-running a batch_id replays from DynamoDB (~0.8s/account, 0 model calls,
  no browser sessions).
- Deterministic modules are pure: no time, randomness, or network.
- Objective checks live in code; models only ever add judgment.
- Tier lint: `opus` requires `generation: true` — enforced at config load.

## Observability (Phase 5)

The engine times every checkpoint and the agent factory reports token usage
per stage. Each run persists `_trace.json` beside its artifacts; the manifest
response carries the aggregated cost-per-stage table, and
`scripts/cost_report.py <batch> <bdr> <account>` prints it after the fact.
OTEL/X-Ray traces are on by default via AgentCore Observability
(`/aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT`).

## Gateway tools (Phase 5)

Internal tools are MCP tools behind the `{prefix}-gateway` (AWS_IAM inbound
auth — SigV4, no client secrets). First tool: `crm_lookup` (mock CRM Lambda)
proving poc1's deferred §4.3 enrichment contract; `pipelines/enrich_demo.yaml`
calls it from a pipeline stage and reports which missing validation fields the
CRM can fill. The auto-apply → re-validate loop-back stays on the roadmap.

## Other pipelines

- `pipelines/demo.yaml` — 2-stage policy demo, zero AWS.
- `pipelines/identify_only.yaml` — identify lane alone (real-account browser runs).
- `pipelines/enrich_demo.yaml` — the §4.3 gateway enrichment demo.
