#!/usr/bin/env python3
"""Invoke the deployed AgentCore Runtime agent with one Account.

    python scripts/invoke_agentcore.py [path/to/account.json] [--batch BATCH_ID]

This is the exact call path the Step Functions batch envelope uses (per-Account
invoke of the AgentCore runtime). Prints the manifest returned by the runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from deploy import config as C

REGION = C.region()
RUNTIME_ARN = C.runtime_arn()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("account", nargs="?", default="mocks/sample_account.json")
    p.add_argument("--batch", default="batch-agentcore-001")
    p.add_argument("--pipeline", default=None,
                   help="pipeline YAML inside the image (default: bdr_outreach)")
    args = p.parse_args()

    account = json.loads(Path(args.account).read_text())
    body_payload = {"account": account, "batch_id": args.batch}
    if args.pipeline:
        body_payload["pipeline"] = args.pipeline
    payload = json.dumps(body_payload)

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    print(f"Invoking {RUNTIME_ARN.split('/')[-1]} ...")
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN, qualifier="DEFAULT", payload=payload
    )
    body = resp["response"].read()
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, indent=2, default=str))
    except json.JSONDecodeError:
        print(body.decode(errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
