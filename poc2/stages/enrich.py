"""Contact Enrichment judgment — Sonnet (agentic half of hris_committee;
ported from poc1). Supplies only the judgment fields the deterministic module
cannot compute — anchor quality above all — and never fabricates.

No pages -> empty Enrichment WITHOUT a model call (nothing to judge; a call
would invite fabrication). That guard is code, not prompt.
"""
from __future__ import annotations

from poc2 import bedrock
from poc2.models import Enrichment
from poc2.pipeline.registry import register
from poc2.stages.common import account_from, load_prompt


@register("agent", "enrich_contacts")
def enrich_contacts(ctx) -> Enrichment:
    pages = ctx.outputs.get("fetch_pages") or []
    if not pages:
        return Enrichment()
    account = account_from(ctx.payload)
    agent = bedrock.make_agent(ctx.stage.tier, load_prompt(ctx.stage.prompt))
    page_block = "\n\n".join(
        f"--- PAGE {i + 1}: {p.get('url')} ---\n{p.get('text', '')}"
        for i, p in enumerate(pages)
    )
    prompt = (
        f"Company: {account.name} ({account.domain or 'domain unknown'}), "
        f"size_band={account.size_band or 'unknown'}\n\n"
        f"Fetched pages:\n\n{page_block}"
    )
    return agent.structured_output(Enrichment, prompt)
