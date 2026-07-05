# BDR Outreach Agents — PoC 2

Config-driven, AgentCore-native rebuild of the BambooHR BDR outreach
artifact-generation workflow. Same product as
[`ejs-sales-agents-poc`](https://github.com/eddie5086/ejs-sales-agents-poc)
(Account in → 3 contacts identified/verified/selected → research → summary →
email/LinkedIn/talk-track per contact → S3, idempotent replay), rebuilt on two
new axes:

1. **Everything configurable** — a versioned, declarative pipeline config
   (`pipelines/*.yaml`) drives every stage: enabled/strategy/model-tier/prompt/
   params. Changing behavior requires no code edits.
2. **AgentCore-native** — AgentCore **Runtime** (hosting), **Browser tool**
   (live web search/fetch — no third-party search API), **Memory** (BDR voice +
   account history), **Gateway** (internal tools as MCP), **Observability**.

**Installable on any AWS account**: every resource name derives from
`config.json` (see [`MIGRATION.md`](MIGRATION.md)); the installer grows in
lockstep with the stack — any PR that adds an AWS resource extends
`scripts/install.py` in the same PR.

For orientation, current status, and conventions read [`CLAUDE.md`](CLAUDE.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

pytest -q                          # offline tests
python -m poc2.run pipelines/demo.yaml    # run a pipeline locally
python -m deploy.config            # print resolved per-account resource names
python scripts/install.py          # preflight + deploy (grows per phase)
```
