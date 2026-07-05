"""Account Validation — Haiku advisory + deterministic field gate (ported).

Required-field presence is an objective fact, not a judgment call, so the
deterministic check is authoritative for status in BOTH directions. poc1's
Haiku was observed to hallucinate a missing field that was present (and to
flag NEEDS_ENRICHMENT with an empty list); trusting its field-level claims
made validation flaky. The model call supplies only advisory `reasons`.
"""
from __future__ import annotations

from poc2 import bedrock
from poc2.models import AccountValidation, PROMPT_EXCLUDE
from poc2.pipeline.registry import register
from poc2.stages.common import account_from, load_prompt

DEFAULT_REQUIRED = ["name", "domain", "industry", "size_band", "hq_region"]


@register("agent", "deterministic_fields")
def validate_account(ctx) -> AccountValidation:
    account = account_from(ctx.payload)
    required = ctx.params.get("required_fields", DEFAULT_REQUIRED)

    agent = bedrock.make_agent(ctx.stage.tier, load_prompt(ctx.stage.prompt))
    prompt = (
        "Validate this Account record:\n"
        f"{account.model_dump_json(exclude=PROMPT_EXCLUDE, indent=2)}"
    )
    result = agent.structured_output(AccountValidation, prompt)

    # Objective check in code — the model only writes advisory reasons.
    truly_missing = [f for f in required if not getattr(account, f, None)]
    result.missing_fields = truly_missing
    result.status = "NEEDS_ENRICHMENT" if truly_missing else "VALID"
    return result
