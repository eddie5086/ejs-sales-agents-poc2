#!/usr/bin/env python3
"""Deploy the AgentCore Gateway + its first MCP tool (Phase 5). Re-runnable.

    python scripts/deploy_gateway.py

Creates (idempotently):
  - the mock CRM-lookup Lambda ({prefix}-crm-lookup) + logs role
  - an IAM role the gateway assumes to invoke that Lambda
  - the MCP gateway ({prefix}-gateway) with AWS_IAM inbound auth — callers
    SigV4-sign requests, so no Cognito client secrets anywhere
  - a lambda gateway target exposing the `crm_lookup` tool

The gateway URL is auto-discovered from the name at runtime (MIGRATION §4).
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from deploy import config as C

REGION = C.region()
ROOT = Path(__file__).resolve().parent.parent

iam = boto3.client("iam", region_name=REGION)
aws_lambda = boto3.client("lambda", region_name=REGION)
control = boto3.client("bedrock-agentcore-control", region_name=REGION)

TOOL_SCHEMA = [{
    "name": "crm_lookup",
    "description": "Look up an account's CRM record by company domain. "
                   "Returns firmographic enrichment fields (industry, "
                   "size_band, hq_region, owner, stage) when the CRM knows "
                   "the domain.",
    "inputSchema": {
        "type": "object",
        "properties": {"domain": {"type": "string",
                                  "description": "company domain, e.g. acme.com"}},
        "required": ["domain"],
    },
}]


def ensure_role(name: str, trust_service: str, inline_name: str, policy: dict) -> str:
    trust = {"Version": "2012-10-17",
             "Statement": [{"Effect": "Allow",
                            "Principal": {"Service": trust_service},
                            "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.create_role(RoleName=name,
                              AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        print(f"  created role {name}")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        print(f"  role {name} exists")
    iam.put_role_policy(RoleName=name, PolicyName=inline_name,
                        PolicyDocument=json.dumps(policy))
    return arn


def ensure_crm_lambda() -> str:
    role_arn = ensure_role(
        C.crm_lambda_role(), "lambda.amazonaws.com", "logs",
        {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "*"}]})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("handler.py", (ROOT / "lambda" / "crm_lookup" / "handler.py").read_text())
    code = buf.getvalue()
    try:
        aws_lambda.create_function(
            FunctionName=C.crm_lambda_name(), Runtime="python3.13", Role=role_arn,
            Handler="handler.handler", Timeout=30, MemorySize=128,
            Code={"ZipFile": code})
        print(f"  created lambda {C.crm_lambda_name()}")
    except aws_lambda.exceptions.ResourceConflictException:
        aws_lambda.update_function_code(FunctionName=C.crm_lambda_name(), ZipFile=code)
        print(f"  updated lambda {C.crm_lambda_name()}")
    aws_lambda.get_waiter("function_active_v2").wait(FunctionName=C.crm_lambda_name())
    return aws_lambda.get_function(
        FunctionName=C.crm_lambda_name())["Configuration"]["FunctionArn"]


def find_gateway() -> dict | None:
    for gw in control.list_gateways().get("items", []):
        if gw.get("name") == C.gateway_name():
            return gw
    return None


def main() -> int:
    print("CRM lambda ...")
    fn_arn = ensure_crm_lambda()

    print("Gateway role ...")
    gw_role_arn = ensure_role(
        C.gateway_role(), "bedrock-agentcore.amazonaws.com", "invoke-crm-lambda",
        {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "lambda:InvokeFunction",
             "Resource": [fn_arn, fn_arn + ":*"]}]})

    print("Gateway ...")
    gw = find_gateway()
    if gw:
        gateway_id = gw["gatewayId"]
        print(f"  gateway {C.gateway_name()} exists ({gateway_id})")
    else:
        created = control.create_gateway(
            name=C.gateway_name(),
            description="poc2 internal tools as MCP (mock CRM enrichment first)",
            roleArn=gw_role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
        )
        gateway_id = created["gatewayId"]
        print(f"  created gateway {gateway_id}")
    while True:
        desc = control.get_gateway(gatewayIdentifier=gateway_id)
        if desc["status"] in ("READY", "FAILED"):
            break
        time.sleep(5)
    if desc["status"] != "READY":
        print(f"  gateway status {desc['status']}: {desc.get('statusReasons')}")
        return 1
    print(f"  gateway READY: {desc['gatewayUrl']}")

    print("Gateway target (crm_lookup) ...")
    existing = control.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    target_cfg = {"mcp": {"lambda": {
        "lambdaArn": fn_arn,
        "toolSchema": {"inlinePayload": TOOL_SCHEMA}}}}
    creds = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    match = [t for t in existing if t.get("name") == "crm"]
    if match:
        control.update_gateway_target(
            gatewayIdentifier=gateway_id, targetId=match[0]["targetId"], name="crm",
            targetConfiguration=target_cfg, credentialProviderConfigurations=creds)
        print("  updated target 'crm'")
    else:
        # A freshly-READY gateway (or its just-created role) can transiently
        # reject target creation — retry briefly before giving up.
        for attempt in range(4):
            try:
                control.create_gateway_target(
                    gatewayIdentifier=gateway_id, name="crm",
                    description="mock CRM lookup (poc1 §4.3 enrichment contract)",
                    targetConfiguration=target_cfg,
                    credentialProviderConfigurations=creds)
                print("  created target 'crm'")
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"  target create retry ({type(e).__name__}); waiting 10s")
                time.sleep(10)

    print("\nDONE — gateway:", C.gateway_name())
    print("gateway_url:", desc["gatewayUrl"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
