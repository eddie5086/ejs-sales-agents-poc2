"""Account enrichment via the Gateway's CRM tool (Phase 5).

Proves poc1's deferred §4.3 contract from a pipeline stage: fetch the CRM
record for the account's domain through the AgentCore Gateway (MCP tools/call)
and report which of the validation-required fields it can fill. Deterministic
merge — no model involved; the stage output is checkpointed, so replays never
re-call the gateway.
"""
from __future__ import annotations

from poc2.pipeline.registry import register
from poc2.stages.common import account_from

ENRICHABLE_FIELDS = ("industry", "size_band", "hq_region")


@register("tool", "gateway_crm_lookup")
def gateway_crm_lookup(ctx) -> dict:
    from poc2 import gateway

    account = account_from(ctx.payload)
    record = gateway.invoke_tool("crm_lookup", {"domain": account.domain or ""})

    filled = {}
    if record.get("found"):
        for field in ctx.params.get("fields", list(ENRICHABLE_FIELDS)):
            if not getattr(account, field, None) and record.get(field):
                filled[field] = record[field]
    return {
        "found": bool(record.get("found")),
        "crm_record": record,
        "filled_fields": filled,           # what §4.3 enrichment would apply
        "still_missing": [
            f for f in ctx.params.get("fields", list(ENRICHABLE_FIELDS))
            if not getattr(account, f, None) and f not in filled
        ],
    }
