"""Account Summarizer — Sonnet (ported from poc1).

Emits the exactly-N-bullet (5) Account brief all three generators share as
context. The "exactly N" contract is defended in code against model drift.
"""
from __future__ import annotations

from poc2 import bedrock
from poc2.models import PROMPT_EXCLUDE, AccountSummary, ResearchResult
from poc2.pipeline.registry import register
from poc2.stages.common import account_from, load_prompt


@register("agent", "five_bullets")
def summarize_account(ctx) -> AccountSummary:
    account = account_from(ctx.payload)
    research = ResearchResult.model_validate(ctx.outputs["research"])
    bullets = int(ctx.params.get("bullets", 5))

    agent = bedrock.make_agent(ctx.stage.tier, load_prompt(ctx.stage.prompt))
    prompt = (
        f"Account:\n{account.model_dump_json(exclude=PROMPT_EXCLUDE, indent=2)}\n\n"
        f"Research:\n{research.model_dump_json(indent=2)}\n\n"
        f"Write the {bullets}-bullet brief."
    )
    summary = agent.structured_output(AccountSummary, prompt)
    summary.bullets = summary.bullets[:bullets]
    return summary
