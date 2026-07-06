# Architecture — as built (all six phases shipped)

Companion docs: `WORKFLOW.md` (per-stage behavior), `ACCOUNT-LIFECYCLE.md`
(one account end to end with real numbers), `PARITY.md` (matrix vs poc1).

## The deployed system

```
Step Functions  bdr-poc2-batch-envelope     (STANDARD; Map over accounts,
   │                                         MaxConcurrency 3, 50% tolerance)
   └─ Lambda  bdr-poc2-invoke-account       (SFN has no native AgentCore
        │                                    integration; proxy calls
        │                                    invoke_agent_runtime; threads
        │                                    batch_id + param_overrides through)
        └─ AgentCore Runtime  bdr_poc2_account_runner   (container via CodeBuild)
             │   PIPELINE ENGINE: loads the payload-named pipeline
             │   (default pipelines/bdr_outreach.yaml), lints it, executes it
             ├─ AgentCore Browser  aws.browser.v1       identify-lane live fetch
             │                                          (Playwright over CDP)
             ├─ AgentCore Memory   bdr_poc2_memory      BDR voice exemplars +
             │                                          per-account event history
             ├─ AgentCore Gateway  bdr-poc2-gateway     internal tools as MCP
             │      └─ Lambda bdr-poc2-crm-lookup       (AWS_IAM auth — SigV4,
             │                                           zero client secrets)
             ├─ AgentCore Observability                 OTEL/X-Ray on by default
             │      + engine-level per-stage trace      (_trace.json, cost table)
             ├─ Bedrock models      haiku 0.0 / sonnet 0.3 / opus 0.7 temps,
             │                      adaptive retries (max 8)
             ├─ DynamoDB bdr-poc2-state                 write-once checkpoints
             └─ S3 bdr-poc2-artifacts-{accountId}       poc1 §11.2 key layout
```

The same engine + config runs locally: in-memory state, local `out/` dir,
fixture fetch, static voice, memory/gateway disabled by empty env. Config
selects implementations, code is identical.

## The pipeline engine (the core new thing vs poc1)

One versioned YAML defines every stage; the runner is an engine that
interprets it. Schema (pydantic, `poc2/pipeline/schema.py`):

```yaml
name: bdr_outreach
stages:
  - id: <unique>
    kind: policy | agent | tool | composite
    tier: haiku | sonnet | opus        # agents require one; opus needs generation: true
    strategy: <registry key>           # plain function per (kind, strategy)
    prompt: prompts/<file>.md          # existence checked at load
    params: {...}                      # any string 'prompts/…' is checked too
    generation: bool                   # the opus tier-lint flag
    fan_out: per_contact               # items from a named provider (params.items_from)
    artifacts: [email, linkedin, talk_track]   # multiplies the fan
    stages: [child, ids]               # composite children (declared stages)
    checkpoint: false                  # idempotent persist stages re-run on replay
flow:
  - {parallel: [validate, identify]}   # threaded group
  - verify
  - reconcile
  - {barrier: {require: [account_valid, three_verified]}}   # condition registry
  - research
  - summary
  - generate
  - persist
```

**Engine rules (all verified in production):**
- Every stage execution wraps `state.checkpoint(batch, account, stage_key)`;
  fan-outs checkpoint per item (`verify#c-101`, `gen#c-102#email`). Write-once,
  replay is a no-op: cold ~36s/account → replay 0.8s, 0 model calls.
- Config lint failures are startup errors: duplicate ids, dangling flow refs,
  missing prompt files, opus-without-generation, fan_out on non-fannable kinds,
  agent without tier.
- Registries (`poc2/pipeline/registry.py`): strategies by `(kind, strategy)`,
  barrier conditions by name, fan-out item providers by name
  (`contact_pool` = CRM + identified; `selected_contacts` = the reconciled 3).
- Invoke-time `param_overrides` (`payload → SFN ItemSelector → Lambda →
  runtime`) merge over stage params without touching the YAML — e.g.
  `run_batch.py --fixture-only` pins the fetch chain for deterministic parity
  runs. Stored checkpoints always win, so overrides never rewrite history.
- The engine times every checkpoint and collects per-stage token usage from
  the agent factory (`_TrackedAgent`) into `RunResult.trace`.

Pipelines shipped: `bdr_outreach.yaml` (the product), `identify_only.yaml`
(real-account browser runs), `enrich_demo.yaml` (§4.3 gateway enrichment),
`demo.yaml` (2-stage policy demo, zero AWS). Same stage registry — different
flows are just different config.

## AgentCore service usage — as deployed

| Service | Used for | Local fallback | Auth/identity notes |
|---|---|---|---|
| Runtime | hosts the engine (ARM64 container, CodeBuild) | engine in-process | env from `deploy.config.runtime_env` |
| Browser tool | identify-lane fetch: team pages + role-targeted LinkedIn SERPs + signals (replaces Exa — user reversal) | `mocks/pages/<domain>.json` | `BdrBrowserTool` role policy; best-effort, falls through the chain |
| Memory | voice exemplars (`bdr/{bdr_id}`·`voice`) + account events (`acct/{id}`·`{batch}`) | static voice snippet; events no-op | id auto-discovered from name; `MEMORY_NAME` empty = off; actor ids forbid `#` — use `/` |
| Gateway | internal tools as MCP; first: `crm_lookup` mock CRM (§4.3) | tool stages raise clearly | AWS_IAM authorizer → SigV4, no secrets; tool names `{target}___{tool}` |
| Observability | OTEL/X-Ray traces (default-on) + engine cost-per-stage (`_trace.json`, `cost_report.py`) | stdout table after local runs | prices per MTok in `poc2/observability.py` |

## Determinism & replay invariants (held throughout)

1. Anything not an LLM/tool call is pure: no time, randomness, or network in
   policy stages (the verbatim HRIS prioritizer's guarantee generalizes).
2. External fetches (browser) checkpoint their RAW OUTPUT; downstream stages
   consume the checkpoint — replays are byte-identical and never re-fetch,
   never reopen a browser, never re-read memory.
3. LLM judgment happens inside checkpointed stages only.
4. Artifact writes are idempotent by key (`checkpoint: false` stages re-run
   every replay — a wiped output location self-heals).

## Resource identity & migration

Every resource name derives from `resource_prefix` in `config.json`
(MIGRATION.md): table `-state`, Lambda `-invoke-account`, SFN
`-batch-envelope`, gateway `-gateway`, CRM lambda `-crm-lookup`, memory
`{prefix}_memory` (underscores — AgentCore naming), bucket
`-artifacts-{accountId}` (globally unique by construction). Auto-discovered,
never configured: account id (STS), runtime ARN + execution role
(`.bedrock_agentcore.yaml`), memory id and gateway URL (matched by name).
`python -m deploy.config` prints the resolved set; the Phase 5 foreign-prefix
dry run (`bdr-poc2b`) re-derived all 16 names from config alone.
`install.py` preflights models + Browser/Memory/Gateway region availability
before creating anything; `uninstall.py` tears down everything it made.
