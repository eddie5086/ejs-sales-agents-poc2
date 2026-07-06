"""Lambda proxy: Step Functions -> AgentCore Runtime.

Step Functions has no native AgentCore integration, so the batch envelope's
per-Account Map state invokes this Lambda, which calls the runtime and returns
the manifest. One Lambda invocation == one Account's agentic loop.

Event shape (from the Map ItemSelector):
    { "account": {..Account..}, "batch_id": "batch-...", "param_overrides": {...} }
"""
import json
import os

import boto3

_client = boto3.client("bedrock-agentcore")
RUNTIME_ARN = os.environ["RUNTIME_ARN"]


def handler(event, context):
    account = event["account"]
    batch_id = event.get("batch_id", "batch-sfn-001")
    resp = _client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        qualifier="DEFAULT",
        payload=json.dumps({"account": account, "batch_id": batch_id,
                            "param_overrides": event.get("param_overrides") or {}}),
    )
    return json.loads(resp["response"].read())
