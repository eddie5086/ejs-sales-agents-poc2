#!/usr/bin/env python3
"""Deploy the Step Functions batch envelope + its invoke-account Lambda proxy.

Creates (idempotently):
  - IAM role for the Lambda (logs + bedrock-agentcore:InvokeAgentRuntime)
  - the invoke-account Lambda (boto3 bundled so the bedrock-agentcore client
    is guaranteed present regardless of the runtime's built-in SDK version)
  - IAM role for Step Functions (lambda:InvokeFunction)
  - the STANDARD state machine from poc/orchestration/batch_envelope.asl.json

Prints the state machine ARN. Re-runnable: updates code/definition in place.

    python scripts/deploy_stepfunctions.py
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from deploy import config as C

REGION = C.region()
LAMBDA_NAME = C.lambda_name()
LAMBDA_ROLE = C.lambda_role()
SFN_NAME = C.sfn_name()
SFN_ROLE = C.sfn_role()
ROOT = Path(__file__).resolve().parent.parent

iam = boto3.client("iam", region_name=REGION)
aws_lambda = boto3.client("lambda", region_name=REGION)
sfn = boto3.client("stepfunctions", region_name=REGION)
ACCOUNT = C.account_id()


def runtime_arn() -> str:
    return C.runtime_arn()


def ensure_role(name: str, trust_service: str, inline_name: str, policy: dict) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": trust_service},
                       "Action": "sts:AssumeRole"}],
    }
    try:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        print(f"  created role {name}")
        time.sleep(10)  # let the role propagate
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        print(f"  role {name} exists")
    iam.put_role_policy(RoleName=name, PolicyName=inline_name, PolicyDocument=json.dumps(policy))
    return arn


def build_lambda_zip() -> bytes:
    """Bundle handler.py + a current boto3 into a zip."""
    with tempfile.TemporaryDirectory() as d:
        print("  pip install boto3 into package ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "boto3", "-t", d],
                       check=True)
        (Path(d) / "handler.py").write_text(
            (ROOT / "lambda" / "invoke_account" / "handler.py").read_text()
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in Path(d).rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(d))
        return buf.getvalue()


def main() -> int:
    rt_arn = runtime_arn()
    print("Runtime ARN:", rt_arn)

    print("IAM roles ...")
    lambda_role_arn = ensure_role(
        LAMBDA_ROLE, "lambda.amazonaws.com", "invoke-runtime",
        {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                           "logs:PutLogEvents"], "Resource": "*"},
            {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime",
             "Resource": [rt_arn, rt_arn + "/*"]},
        ]},
    )

    print("Lambda ...")
    code = build_lambda_zip()
    common = dict(FunctionName=LAMBDA_NAME, Runtime="python3.13", Role=lambda_role_arn,
                  Handler="handler.handler", Timeout=300, MemorySize=512,
                  Environment={"Variables": {"RUNTIME_ARN": rt_arn}})
    try:
        aws_lambda.create_function(**common, Code={"ZipFile": code})
        print("  created function")
    except aws_lambda.exceptions.ResourceConflictException:
        aws_lambda.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=code)
        aws_lambda.get_waiter("function_updated").wait(FunctionName=LAMBDA_NAME)
        aws_lambda.update_function_configuration(
            FunctionName=LAMBDA_NAME, Timeout=300, MemorySize=512,
            Environment={"Variables": {"RUNTIME_ARN": rt_arn}})
        print("  updated function")
    aws_lambda.get_waiter("function_active_v2").wait(FunctionName=LAMBDA_NAME)
    fn_arn = aws_lambda.get_function(FunctionName=LAMBDA_NAME)["Configuration"]["FunctionArn"]

    print("Step Functions role ...")
    sfn_role_arn = ensure_role(
        SFN_ROLE, "states.amazonaws.com", "invoke-lambda",
        {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "lambda:InvokeFunction",
             "Resource": [fn_arn, fn_arn + ":*"]},
        ]},
    )

    print("State machine ...")
    definition = (ROOT / "poc2" / "orchestration" / "batch_envelope.asl.json").read_text()
    definition = definition.replace("${InvokeAccountFunctionArn}", fn_arn)
    sm_arn = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{SFN_NAME}"
    try:
        out = sfn.create_state_machine(name=SFN_NAME, definition=definition,
                                       roleArn=sfn_role_arn, type="STANDARD")
        sm_arn = out["stateMachineArn"]
        print("  created state machine")
    except sfn.exceptions.StateMachineAlreadyExists:
        sfn.update_state_machine(stateMachineArn=sm_arn, definition=definition,
                                 roleArn=sfn_role_arn)
        print("  updated state machine")

    print("\nDONE")
    print("state_machine_arn:", sm_arn)
    print("lambda_arn:", fn_arn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
