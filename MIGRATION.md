# Migration contract — installable on any AWS account, from Phase 0

This is a REQUIREMENT the implementation must honor at every phase, not a
feature to add later. Rule: **any PR that introduces an AWS resource extends
`scripts/install.py` and `deploy/config.py` in the same PR.** The installer is
never "caught up".

## The contract

1. **One file to migrate**: editing `config.json` and running
   `python scripts/install.py` against fresh credentials installs the whole
   stack on any AWS account. Nothing account-specific lives anywhere else.
2. **Every resource name derives from `resource_prefix`** (table, Lambda,
   state machine, roles, memory store, gateway, ECR repo via agent_name).
   Changing the prefix runs independent stacks side by side in one account.
3. **S3 buckets are globally unique by construction**: `artifact_bucket: ""`
   auto-derives `{prefix}-artifacts-{accountId}`; an explicit name overrides.
   ANY future bucket follows the same derive-or-override rule.
4. **Auto-discovered, never configured**: AWS account id (STS), runtime ARN and
   execution role (read back from `.bedrock_agentcore.yaml` after deploy),
   Gateway/Memory identifiers (read back post-create).
5. **Idempotent + order-independent installers**: each deploy script is safely
   re-runnable; `install.py` orchestrates them and PREFLIGHTS (model access,
   region service availability for AgentCore Browser/Memory/Gateway) before
   creating anything.
6. **Scripted teardown**: `scripts/uninstall.py` removes everything the
   installer created, derived from the same config.
7. **Region coupling**: model IDs carry a region-family prefix (`us.`/`eu.`/
   `apac.`) — MIGRATION docs and preflight must catch region/model mismatch.
8. **Verification**: `python -m deploy.config` prints the fully-resolved
   resource set; per-phase PRs must show it resolving correctly, and Phase 5
   does a foreign-account dry-run review.

## config.json (initial values)

```json
{
  "aws_region": "us-east-1",
  "resource_prefix": "bdr-poc2",
  "agent_name": "bdr_poc2_account_runner",
  "artifact_bucket": "",
  "models": {
    "haiku":  "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "opus":   "us.anthropic.claude-opus-4-6-v1"
  }
}
```

| Field | Rule |
|---|---|
| `aws_region` | Coupled to model-ID prefixes; preflight validates. |
| `resource_prefix` | Lowercase, hyphens ok; drives every resource name. |
| `agent_name` | AgentCore rule: **underscores only, no hyphens**; also names the ECR repo. |
| `artifact_bucket` | Empty → auto-derive `{prefix}-artifacts-{accountId}`; explicit name wins. |
| `models` | Must be invocable on the target account (see docs/AWS-GOTCHAS.md §1). |

Reference implementation to port: `../ejs-sales-agents-poc/deploy/config.py`,
`scripts/install.py`, and its `MIGRATION.md` (proven on the dev account).
