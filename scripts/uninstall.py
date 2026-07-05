#!/usr/bin/env python3
"""Scripted teardown of everything the installer creates, derived from the
same config (MIGRATION.md §6).

    python scripts/uninstall.py [--yes]

Enumerates the derived resource set and deletes each IF it exists. Safe to run
at any phase: resources not yet created (or already gone) are reported and
skipped. Order: state machine -> Lambda -> DynamoDB -> S3 bucket -> ECR repo ->
IAM roles -> AgentCore runtime.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boto3
from botocore.exceptions import ClientError

from deploy import config as C


def _gone(kind: str, name: str) -> None:
    print(f"  --    {kind} '{name}' does not exist, skipping")


def _deleted(kind: str, name: str) -> None:
    print(f"  DEL   {kind} '{name}'")


def delete_state_machine() -> None:
    sfn = boto3.client("stepfunctions", region_name=C.region())
    arn = f"arn:aws:states:{C.region()}:{C.account_id()}:stateMachine:{C.sfn_name()}"
    try:
        sfn.describe_state_machine(stateMachineArn=arn)
    except ClientError:
        return _gone("state machine", C.sfn_name())
    sfn.delete_state_machine(stateMachineArn=arn)
    _deleted("state machine", C.sfn_name())


def delete_lambda() -> None:
    lam = boto3.client("lambda", region_name=C.region())
    try:
        lam.delete_function(FunctionName=C.lambda_name())
        _deleted("lambda", C.lambda_name())
    except lam.exceptions.ResourceNotFoundException:
        _gone("lambda", C.lambda_name())


def delete_table() -> None:
    ddb = boto3.client("dynamodb", region_name=C.region())
    try:
        ddb.delete_table(TableName=C.table())
        _deleted("dynamodb table", C.table())
    except ddb.exceptions.ResourceNotFoundException:
        _gone("dynamodb table", C.table())


def delete_bucket() -> None:
    s3 = boto3.resource("s3", region_name=C.region())
    bucket = s3.Bucket(C.bucket())
    try:
        s3.meta.client.head_bucket(Bucket=C.bucket())
    except ClientError:
        return _gone("s3 bucket", C.bucket())
    bucket.objects.all().delete()
    bucket.delete()
    _deleted("s3 bucket", C.bucket())


def delete_ecr_repo() -> None:
    ecr = boto3.client("ecr", region_name=C.region())
    try:
        ecr.delete_repository(repositoryName=C.ecr_repo(), force=True)
        _deleted("ecr repo", C.ecr_repo())
    except ecr.exceptions.RepositoryNotFoundException:
        _gone("ecr repo", C.ecr_repo())


def delete_roles() -> None:
    iam = boto3.client("iam", region_name=C.region())
    for role in (C.lambda_role(), C.sfn_role()):
        try:
            for pol in iam.list_role_policies(RoleName=role)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role, PolicyName=pol)
            for att in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role, PolicyArn=att["PolicyArn"])
            iam.delete_role(RoleName=role)
            _deleted("iam role", role)
        except iam.exceptions.NoSuchEntityException:
            _gone("iam role", role)


def delete_agent_runtime() -> None:
    """Delete the AgentCore runtime matching agent_name, if any."""
    client = boto3.client("bedrock-agentcore-control", region_name=C.region())
    try:
        runtimes = client.list_agent_runtimes().get("agentRuntimes", [])
    except Exception as e:  # service may be unavailable in a region — report, move on
        print(f"  --    could not list AgentCore runtimes ({e}); skipping")
        return
    match = [r for r in runtimes if r.get("agentRuntimeName") == C.agent_name()]
    if not match:
        return _gone("agentcore runtime", C.agent_name())
    client.delete_agent_runtime(agentRuntimeId=match[0]["agentRuntimeId"])
    _deleted("agentcore runtime", C.agent_name())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="skip confirmation")
    args = parser.parse_args()

    print(f"Target account={C.account_id()} region={C.region()} prefix={C.prefix()}")
    print("Will delete (if they exist):")
    r = C.resolved()
    for key in ("state_machine", "lambda_name", "dynamodb_table", "artifact_bucket",
                "ecr_repo", "lambda_role", "state_machine_role", "agent_name"):
        print(f"  {key}: {r[key]}")

    if not args.yes:
        if input("\nType 'delete' to proceed: ").strip() != "delete":
            print("Aborted.")
            return 1

    delete_state_machine()
    delete_lambda()
    delete_table()
    delete_bucket()
    delete_ecr_repo()
    delete_roles()
    delete_agent_runtime()
    print("\nDONE — teardown complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
