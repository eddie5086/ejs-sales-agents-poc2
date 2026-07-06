#!/usr/bin/env python3
"""One-command install of the whole stack onto the account in config.json.

    python scripts/install.py

Runs, in order: config lint, Bedrock model-access preflight, then the runtime
(S3 bucket + AgentCore container + role policies), DynamoDB, and Step Functions
deploys. Each later phase appends its deploy step here IN THE SAME PR that
introduces the resource (MIGRATION.md — the installer is never "caught up").

Uses whatever AWS credentials your environment is configured with — deploys to
THAT account.
"""
from __future__ import annotations

import subprocess
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


def run(script: str) -> None:
    print(f"\n{'=' * 64}\n  {script}\n{'=' * 64}")
    subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True, cwd=ROOT)


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

    run("deploy_agentcore.py")      # S3 bucket + runtime + role policies
    run("deploy_dynamodb.py")       # state table
    run("deploy_stepfunctions.py")  # Lambda proxy + state machine
    # Phase 3+: Browser / Memory / Gateway permissions and stores land here.

    print("\nDONE — stack installed. Smoke test:\n  python scripts/invoke_agentcore.py\n"
          "Batch:\n  python scripts/run_batch.py --batch-id batch-fresh-001")
    return 0


if __name__ == "__main__":
    sys.exit(main())
