"""Strands Agent factory (ported from poc1).

One place to build tier-appropriate agents. Every agent stage builds a Strands
`Agent` backed by a `BedrockModel` — the same object graph that deploys onto
Bedrock AgentCore Runtime, so local runs and cloud runs share code.

Import note: strands/botocore load lazily inside make_agent so offline test
collection (which imports every strategy module to populate the registry)
never touches AWS SDKs.
"""
from __future__ import annotations

from functools import lru_cache

from poc2.config import TIER_TEMPERATURE, settings


@lru_cache(maxsize=None)
def _model(tier: str):
    from botocore.config import Config
    from strands.models import BedrockModel

    # Generation fans out 9 Opus calls per Account, and Step Functions runs
    # several Accounts at once — bursts can trip Bedrock rate limits. Adaptive
    # retries with a generous attempt budget smooth over transient
    # ThrottlingExceptions (docs/AWS-GOTCHAS.md §4).
    return BedrockModel(
        model_id=settings.model_for_tier(tier),
        region_name=settings.aws_region,
        temperature=TIER_TEMPERATURE[tier],
        boto_client_config=Config(retries={"max_attempts": 8, "mode": "adaptive"}),
    )


def make_agent(tier: str, system_prompt: str, tools: list | None = None):
    """Build a Strands agent on the given model tier ('haiku'|'sonnet'|'opus').

    callback_handler=None disables Strands' default token streaming to stdout —
    the generators run concurrently, so their streams would otherwise interleave
    into unreadable output. Saved artifacts are unaffected either way.
    """
    from strands import Agent

    return Agent(
        model=_model(tier),
        system_prompt=system_prompt,
        tools=tools or [],
        callback_handler=None,
    )
