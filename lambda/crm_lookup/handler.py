"""Mock CRM-lookup Lambda — the Gateway's first MCP tool (Phase 5).

Proves poc1's deferred §4.3 enrichment contract: given an account domain,
return the firmographic fields validation flags as missing. Canned data only —
the real CRM integration stays on the post-parity roadmap.

Gateway lambda targets receive the MCP tool arguments as the event; the tool
name arrives in context.client_context.custom['bedrockAgentCoreToolName'].
"""

CRM = {
    "meridianrobotics.com": {
        "industry": "Industrial Automation", "size_band": "201-500",
        "hq_region": "EMEA", "account_owner": "sam.ortiz",
        "crm_stage": "prospect", "last_activity": "2026-05-18",
    },
    "northwindlogistics.com": {
        "industry": "Logistics", "size_band": "51-200",
        "hq_region": "EMEA", "account_owner": "sam.ortiz",
        "crm_stage": "prospect", "last_activity": "2026-06-02",
    },
    "basecamp.com": {
        "industry": "Software", "size_band": "51-200",
        "hq_region": "NA", "account_owner": "jordan.ellis",
        "crm_stage": "new", "last_activity": None,
    },
}


def handler(event, context):
    domain = (event or {}).get("domain", "").strip().lower()
    record = CRM.get(domain)
    if not record:
        return {"found": False, "domain": domain,
                "note": "no CRM record; enrichment must fall back to research"}
    return {"found": True, "domain": domain, "source": "crm", **record}
