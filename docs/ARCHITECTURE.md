# Architecture

## Target system

```
Step Functions  {prefix}-batch-envelope     (STANDARD; Map over accounts,
   │                                         MaxConcurrency 3, 50% tolerance)
   └─ Lambda  {prefix}-invoke-account       (SFN has no native AgentCore
        │                                    integration; proxy calls
        │                                    invoke_agent_runtime)
        └─ AgentCore Runtime  {agent_name}
             │   PIPELINE ENGINE: loads pipelines/bdr_outreach.yaml,
             │   validates it, executes the declared DAG
             ├─ AgentCore Browser tool    live web search/fetch (identify lane)
             ├─ AgentCore Memory          BDR voice + account event history
             ├─ AgentCore Gateway         internal tools as MCP (enrichment/CRM)
             ├─ AgentCore Observability   per-stage OTEL traces
             ├─ Bedrock models            Haiku / Sonnet / Opus tiers
             ├─ DynamoDB {prefix}-state   write-once checkpoint per stage
             └─ S3 {artifact_bucket}      terminal artifacts (poc1 §11.2 layout)
```

The same engine + config runs locally: in-memory state, local `out/` dir,
fixture fetch, static voice. Config selects implementations, code is identical.

## The pipeline engine (the core new thing)

**Config schema** (pydantic-validated at startup):

```yaml
name: bdr_outreach
stages:
  - id: validate
    kind: agent                      # policy | agent | tool | composite
    tier: haiku
    strategy: deterministic_fields   # resolved from the stage registry
    prompt: prompts/validate.md
    params: {required_fields: [name, domain, industry, size_band, hq_region]}
  - id: identify
    kind: composite                  # runs its own child stages, each checkpointed
    stages: [fetch_pages, enrich, prioritize]
    params:
      contacts_needed: 3
      email_policy: high_confidence_only
      fetch: [attached, browser, fixture]      # ordered fallback chain
  - id: verify
    kind: agent
    tier: haiku
    strategy: email_or_phone
    fan_out: per_contact             # engine fans out + checkpoints per item
  - id: reconcile
    kind: policy
    strategy: alphabetical           # swap to priority_first etc. in config
  - id: research   … (sonnet)
  - id: summary    … (sonnet, params: {bullets: 5})
  - id: generate
    kind: agent
    tier: opus
    generation: true                 # REQUIRED for opus — the tier lint
    fan_out: per_contact
    artifacts: [email, linkedin, talk_track]
    params: {voice: memory}          # memory | static
flow:
  - {parallel: [validate, identify]}
  - verify
  - reconcile
  - barrier: {require: [account_valid, three_verified]}
  - research
  - summary
  - generate
  - persist
```

**Engine rules**
- Each stage resolves `(kind, strategy)` in a registry of plain functions —
  poc1's one-module-per-agent pattern survives as strategy implementations.
- Every stage execution is wrapped in `state.checkpoint(batch, account,
  stage_id)`; fan-out stages checkpoint per item (`verify#<contact_id>`,
  `gen#<contact_id>#<artifact>`). Write-once, replay is a no-op (poc1 §9,
  proven: cold ~37s/account → replay <1s).
- `flow` declares ordering; `parallel` groups run threaded; `barrier` entries
  are declarative guards (poc1's sync barrier, now config).
- Config lint failures are startup errors: duplicate ids, dangling flow refs,
  missing prompt files, opus-without-generation, fan_out on non-fannable kinds.
- Prompts are files in `prompts/`, referenced by path — versioned with the
  config, never inlined in code.

## AgentCore service usage

| Service | Used for | Local fallback |
|---|---|---|
| Runtime | hosts the engine (container via CodeBuild) | run engine in-process |
| Browser tool | identify-lane web search + page fetch (replaces poc1's Exa decision — user reversed it for poc2) | `mocks/pages/<domain>.json` fixture |
| Memory | per-BDR voice retrieval; per-account event history | static voice snippet |
| Gateway | internal tools as MCP (mock CRM/enrichment first) | direct in-process call |
| Observability | per-stage token/latency/cost traces | stdout logs |

## Determinism & replay invariants (non-negotiable)

1. Anything not an LLM/tool call must be pure: no time, randomness, or network
   in policy stages (the HRIS prioritizer's guarantee generalizes).
2. External fetches (browser) checkpoint their RAW OUTPUT; downstream stages
   consume the checkpoint, so replays are byte-identical and never re-fetch.
3. LLM judgment happens inside checkpointed stages only.
4. Artifact writes are idempotent by key (S3 layout identical to poc1).
