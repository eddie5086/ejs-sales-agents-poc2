#!/usr/bin/env python3
"""One-command install of the whole stack onto the account in config.json.

    python scripts/install.py

Phase 0: validates config.json, prints the STS identity it would deploy to,
preflights Bedrock model access, and reports that no AWS resources exist yet.
Each later phase appends its deploy step here IN THE SAME PR that introduces
the resource (MIGRATION.md — the installer is never "caught up").

Uses whatever AWS credentials your environment is configured with — deploys to
THAT account.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boto3
from botocore.exceptions import ClientError

from deploy import config as C


def preflight_models() -> bool:
    rt = boto3.client("bedrock-runtime", region_name=C.region())
    ok = True
    for tier, mid in C.models().items():
        try:
            rt.converse(modelId=mid, messages=[{"role": "user", "content": [{"text": "hi"}]}],
                        inferenceConfig={"maxTokens": 5})
            print(f"  OK    {tier}: {mid}")
        except ClientError as e:
            ok = False
            print(f"  FAIL  {tier}: {mid} -> {e.response['Error']['Code']}")
    return ok


def main() -> int:
    print("=== preflight: config.json lint ===")
    problems = C.validate()
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        print("\nFix config.json and re-run. Aborting.")
        return 1
    print("  OK    config.json is well-formed")

    print(f"\nTarget account={C.account_id()} region={C.region()} prefix={C.prefix()}")
    print(f"Artifact bucket: {C.bucket()}")

    print("\n=== preflight: Bedrock model access ===")
    if not preflight_models():
        print("\nOne or more models are not invocable on this account/region. Fix model "
              "access/agreements (see MIGRATION.md and docs/AWS-GOTCHAS.md §1) or edit "
              "config.json models, then re-run. Aborting before creating resources.")
        return 1

    # Phase 0 creates no AWS resources. Later phases append their deploy steps:
    #   Phase 2: deploy_agentcore.py, deploy_dynamodb.py, deploy_stepfunctions.py
    #   Phase 3+: Browser / Memory / Gateway permissions and stores
    print("\nDONE — preflight passed. No AWS resources to create yet (Phase 0).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
