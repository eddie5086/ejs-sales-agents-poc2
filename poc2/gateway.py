"""AgentCore Gateway MCP client (Phase 5).

Calls internal tools exposed by the {prefix}-gateway over MCP (JSON-RPC over
streamable HTTP), SigV4-signed with the caller's IAM credentials — the
gateway uses the AWS_IAM authorizer, so there are no client secrets anywhere.

The gateway URL is auto-discovered from GATEWAY_NAME (MIGRATION §4). Empty
GATEWAY_NAME (every plain local run) = disabled: tool stages raise a clear
error rather than silently no-op — an enrichment result must never be faked.
"""
from __future__ import annotations

import json
import urllib.request
from functools import lru_cache
from typing import Any, Optional

from poc2.config import settings


class GatewayError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def gateway_url() -> Optional[str]:
    if not settings.gateway_name:
        return None
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=settings.aws_region)
    for gw in control.list_gateways().get("items", []):
        if gw.get("name") == settings.gateway_name:
            return control.get_gateway(gatewayIdentifier=gw["gatewayId"])["gatewayUrl"]
    return None


def _signed_request(url: str, body: bytes) -> urllib.request.Request:
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session = boto3.Session()
    creds = session.get_credentials()
    if creds is None:
        raise GatewayError("no AWS credentials available to sign the gateway call")
    aws_req = AWSRequest(method="POST", url=url, data=body,
                         headers={"Content-Type": "application/json"})
    SigV4Auth(creds, "bedrock-agentcore", settings.aws_region).add_auth(aws_req)
    return urllib.request.Request(url, data=body, method="POST",
                                  headers=dict(aws_req.headers))


def invoke_tool(tool: str, arguments: dict, timeout_s: int = 30) -> Any:
    """MCP tools/call. Gateway tool names are '{target}___{tool}'; passing the
    bare tool name resolves it against the 'crm' target by default."""
    url = gateway_url()
    if not url:
        raise GatewayError(
            "gateway not configured (GATEWAY_NAME empty or gateway not found) — "
            "deploy with scripts/deploy_gateway.py; local runs have no gateway")
    name = tool if "___" in tool else f"crm___{tool}"
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    req = _signed_request(url, json.dumps(rpc).encode())
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read())
    if payload.get("error"):
        raise GatewayError(f"gateway rpc error: {payload['error']}")
    result = payload.get("result") or {}
    if result.get("isError"):
        raise GatewayError(f"tool error: {result}")
    # MCP content: list of {type: text, text: ...} — tool output is JSON text.
    for item in result.get("content") or []:
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                return item["text"]
    return result
