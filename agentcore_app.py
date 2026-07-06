"""Bedrock AgentCore Runtime entrypoint.

Wraps the pipeline ENGINE as a deployable AgentCore Runtime agent: one
invocation = one Account run of a declared pipeline (default
pipelines/bdr_outreach.yaml, overridable per invoke). Step Functions
(deployed separately) is the outer batch envelope that fans this out per
Account via the invoke-account Lambda proxy.

Invoke payload:
    { "account": {...Account json...}, "batch_id": "batch-...",
      "pipeline": "pipelines/bdr_outreach.yaml" }        # optional
Response:
    the run manifest (the persist stage's output).

State goes to DynamoDB and artifacts to S3 when STATE_DDB_TABLE /
ARTIFACT_S3_BUCKET are set in the runtime environment (they are, via
deploy.config.runtime_env), else to in-memory / container-local fs.
"""
from __future__ import annotations

from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp

ROOT = Path(__file__).resolve().parent
DEFAULT_PIPELINE = "pipelines/bdr_outreach.yaml"

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(payload: dict) -> dict:
    # Heavy imports (engine -> strategies -> strands) are deferred to first
    # request so cold-start module-import stays under the 30s init limit
    # (docs/AWS-GOTCHAS.md §2).
    import time

    import poc2.stages  # noqa: F401 — populate the strategy registry
    from poc2.pipeline.engine import Engine
    from poc2.pipeline.schema import ConfigError, load_pipeline
    from poc2.state import StateStore

    if "account" not in payload:
        return {"error": "payload must contain an 'account' object"}
    account = payload["account"]
    batch_id = payload.get("batch_id", "batch-agentcore-001")
    pipeline_rel = payload.get("pipeline", DEFAULT_PIPELINE)

    try:
        config = load_pipeline(ROOT / pipeline_rel, base_dir=ROOT)
    except ConfigError as e:
        return {"error": f"pipeline config: {e}"}

    engine = Engine(config, state=StateStore())
    result = engine.run(
        batch_id, account.get("account_id", "unknown"),
        {"account": account, "_started_at": time.time()},
    )
    manifest = result.outputs.get("persist")
    if isinstance(manifest, dict):
        return manifest
    # Pipelines without a persist stage (e.g. demo): return a slim summary.
    return {
        "pipeline": result.pipeline, "batch_id": result.batch_id,
        "account_id": result.account_id,
        "computed": len(result.computed), "cached": len(result.cached),
    }


if __name__ == "__main__":
    app.run()
