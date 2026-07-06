#!/usr/bin/env python3
"""Deploy the pipeline-engine runtime to Bedrock AgentCore Runtime (ported
from poc1).

Self-contained per account: creates the S3 artifact bucket, deploys the runtime
(container build via CodeBuild — no local Docker), then attaches every policy the
auto-created execution role needs (S3 write, ECR pull, DynamoDB state table).
All names/region/models come from config.json via deploy.config.

    python scripts/deploy_agentcore.py

Idempotent: re-run to update the runtime image + refresh role policies.
Gotchas honored (docs/AWS-GOTCHAS.md §2): programmatic Runtime API (the CLI
breaks on piped stdin), container artifact type (direct_code_deploy omits the
SDK), fixtures/pipelines/prompts ship inside the image.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AGENTCORE_SUPPRESS_RECOMMENDATION", "1")

import boto3
from bedrock_agentcore_starter_toolkit import Runtime

from deploy import config as C


def ensure_bucket() -> None:
    s3 = boto3.client("s3", region_name=C.region())
    name, region = C.bucket(), C.region()
    kwargs: dict = {"Bucket": name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
        print(f"  created bucket {name}")
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        print(f"  bucket {name} exists")


def grant_execution_role() -> None:
    """Attach S3 + ECR + DynamoDB inline policies to the toolkit-created runtime
    role (discovered from .bedrock_agentcore.yaml). Bedrock invoke is already on
    the auto-created role. Phases 3-5 grow Browser/Memory/Gateway grants here."""
    iam = boto3.client("iam", region_name=C.region())
    role = C.runtime_execution_role_name()
    region, account = C.region(), C.account_id()
    bucket_arn = f"arn:aws:s3:::{C.bucket()}"
    ecr_arn = f"arn:aws:ecr:{region}:{account}:repository/{C.ecr_repo()}"
    table_arn = f"arn:aws:dynamodb:{region}:{account}:table/{C.table()}"

    policies = {
        "BdrArtifactsS3Write": [
            {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
             "Resource": [bucket_arn, bucket_arn + "/*"]},
        ],
        "BdrEcrPull": [
            {"Effect": "Allow",
             "Action": ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
                        "ecr:BatchCheckLayerAvailability"],
             "Resource": ecr_arn},
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        ],
        "BdrStateTable": [
            {"Effect": "Allow",
             "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query",
                        "dynamodb:UpdateItem", "dynamodb:BatchGetItem"],
             "Resource": [table_arn, table_arn + "/index/*"]},
        ],
        # Phase 3: the identify lane's fetch chain opens AgentCore Browser
        # sessions (aws.browser.v1). Session ARNs are dynamic -> resource *.
        "BdrBrowserTool": [
            {"Effect": "Allow",
             "Action": ["bedrock-agentcore:StartBrowserSession",
                        "bedrock-agentcore:GetBrowserSession",
                        "bedrock-agentcore:StopBrowserSession",
                        "bedrock-agentcore:ListBrowserSessions",
                        "bedrock-agentcore:ConnectBrowserAutomationStream",
                        "bedrock-agentcore:UpdateBrowserStream"],
             "Resource": "*"},
        ],
        # Phase 4: voice retrieval + account event history in AgentCore
        # Memory (memory id carries a random suffix -> resource *).
        "BdrMemory": [
            {"Effect": "Allow",
             "Action": ["bedrock-agentcore:ListMemories",
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:ListSessions",
                        "bedrock-agentcore:ListActors",
                        "bedrock-agentcore:RetrieveMemoryRecords"],
             "Resource": "*"},
        ],
    }
    for name, statements in policies.items():
        iam.put_role_policy(RoleName=role, PolicyName=name,
                            PolicyDocument=json.dumps({"Version": "2012-10-17",
                                                       "Statement": statements}))
    print(f"  granted {role}: S3 + ECR + DynamoDB + Browser + Memory")


def main() -> int:
    print("=== S3 artifact bucket ===")
    ensure_bucket()

    rt = Runtime()
    print("\n=== configure ===")
    rt.configure(
        entrypoint="agentcore_app.py",
        agent_name=C.agent_name(),
        requirements_file="agentcore-requirements.txt",
        region=C.region(),
        deployment_type="container",  # ARM64 build via CodeBuild (no local Docker)
        auto_create_execution_role=True,
        auto_create_ecr=True,
        auto_create_s3=True,
        memory_mode="NO_MEMORY",
        non_interactive=True,
    )

    print("\n=== launch (deploy to cloud runtime) ===")
    result = rt.launch(env_vars=C.runtime_env(), auto_update_on_conflict=True)
    print("launched:", json.dumps(getattr(result, "__dict__", {}), default=str, indent=2))

    print("\n=== grant execution-role policies ===")
    grant_execution_role()

    print("\n=== status ===")
    status = rt.status()
    ready = json.dumps(getattr(status, "__dict__", {}), default=str)
    print("READY" if '"status": "READY"' in ready or "'status': 'READY'" in ready else ready[:400])
    print("\nruntime_arn:", C.runtime_arn())
    return 0


if __name__ == "__main__":
    sys.exit(main())
