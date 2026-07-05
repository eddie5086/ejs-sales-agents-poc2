"""Research Agent — Sonnet (ported from poc1).

Produces company facts + trigger events that anchor personalization. No live
tools yet (Browser lands in Phase 3): it reasons from the Account record only
and tags each finding's origin so the provenance story stays honest.
"""
from __future__ import annotations

from poc2 import bedrock
from poc2.models import PROMPT_EXCLUDE, ResearchResult
from poc2.pipeline.registry import register
from poc2.stages.common import account_from, load_prompt


@register("agent", "account_research")
def research_account(ctx) -> ResearchResult:
    account = account_from(ctx.payload)
    agent = bedrock.make_agent(ctx.stage.tier, load_prompt(ctx.stage.prompt))
    prompt = (
        "Research this Account for first-touch outreach:\n"
        f"{account.model_dump_json(exclude=PROMPT_EXCLUDE, indent=2)}"
    )
    return agent.structured_output(ResearchResult, prompt)
