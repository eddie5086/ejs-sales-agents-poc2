# AWS gotchas — read before any deploy work

Everything below cost real debugging time in poc1. A fresh session has no
memory of it; this file is the memory.

## 1. Bedrock model access (per-account, not a console toggle)

- Tier IDs verified invocable on dev account 296497502276 / us-east-1:
  - haiku: `us.anthropic.claude-haiku-4-5-20251001-v1:0`
  - sonnet: `us.anthropic.claude-sonnet-4-6`
  - opus: `us.anthropic.claude-opus-4-6-v1`
- **Sonnet 5 / Opus 4.8 / Fable 5 are BLOCKED on this account**: their Bedrock
  `agreementAvailability` is `NOT_AVAILABLE` — unlocking is an AWS-Sales
  action, not self-service. Do not burn time retrying; when unblocked, only the
  tier IDs in config change.
- The `us.` inference-profile prefix is region-family-specific (`eu.`,
  `apac.`). Region changes in config must change the model IDs too.
- The installer must PREFLIGHT model invocability and abort before creating
  resources (poc1's `install.py` does this — port it).

## 2. AgentCore Runtime deploy

- The starter-toolkit `configure` CLI **breaks on piped stdin** — use the
  programmatic `Runtime` API (`bedrock_agentcore_starter_toolkit`), as poc1's
  `scripts/deploy_agentcore.py` does.
- `direct_code_deploy` does NOT bundle the SDK → use **container** artifact
  type (CodeBuild builds ARM64 image; no local Docker needed).
- **30-second cold-start init cap**: defer heavy imports (strands, boto3
  clients) INTO the handler function. poc1's `agentcore_app.py` shows the shape.
- Artifact type **cannot switch code↔container in place** — delete the agent
  and recreate.
- The toolkit auto-creates the execution role
  (`AmazonBedrockAgentCoreSDKRuntime-<region>-<hash>`) with Bedrock perms only:
  S3, ECR pull, and DynamoDB policies must be attached post-deploy (poc1's
  deploy script does; poc2 adds Browser/Memory/Gateway permissions per phase).
- Deploy state lands in `.bedrock_agentcore.yaml` (gitignored). Runtime ARN and
  role are READ BACK from it — never hardcode, never put in config.json.
- CodeBuild builds from the working tree: **fixtures/mocks ship inside the
  image** — after changing them, redeploy or the runtime won't see them.

## 3. Step Functions / Lambda envelope

- SFN has **no native AgentCore integration** — a Lambda proxy calls
  `invoke_agent_runtime` (port poc1's `lambda/invoke_account/`). Bundle boto3
  into the Lambda zip (runtime boto3 may predate bedrock-agentcore).
- Batch input carries `batch_id`; re-running the same batch_id is the replay
  path — a fresh batch_id is required to actually exercise new code.

## 4. Model-robustness bugs that only surfaced under the concurrent AWS batch

- LLM structured output can return `null` for a list field; pydantic rejects it
  (defaults only apply when the key is ABSENT). poc1's `SchemaModel` base
  coerces null→[] — port it.
- Haiku hallucinated required-field presence both ways → validation status is
  computed IN CODE; the model only writes advisory reasons.
- The 9-Opus/account generation fan-out × concurrent accounts throttles
  Bedrock → the agent factory uses adaptive retries. Port it.

## 5. Testing / repo hygiene

- Pin `testpaths = tests` in pytest.ini from day one: a stray same-basename
  test file at repo root breaks pytest collection with an import-mismatch
  error (bit poc1 when handoff artifacts were dropped into the working tree).
- Offline tests must run with zero AWS calls; real-Bedrock verification is a
  separate explicit step (`run_local`-equivalent), and end-to-end AWS
  verification uses a fresh batch_id.

## 6. Operational notes

- gh CLI is NOT installed on the dev machine — GitHub operations use the REST
  API with the token from `git credential fill` (osxkeychain).
- Idle cost of the whole stack is ~nil (no always-on compute; DynamoDB/S3
  pay-per-use). Teardown: agent runtime via `agentcore destroy`/toolkit, then
  Lambda, state machine, table, bucket, ECR repo, and the `{prefix}-*` roles —
  poc2's `uninstall.py` scripts this.
- Logs: `/aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT`; X-Ray/OTEL is on
  by default via AgentCore.
