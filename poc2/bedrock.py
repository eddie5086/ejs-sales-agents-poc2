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


class _TrackedAgent:
    """Thin proxy that reports token usage to the per-thread observability
    collector after every model interaction (Phase 5 cost-per-stage traces)."""

    def __init__(self, agent):
        self._agent = agent
        self._seen_in = 0
        self._seen_out = 0

    def _report(self) -> None:
        from poc2 import observability

        usage = self._agent.event_loop_metrics.accumulated_usage
        tin, tout = usage.get("inputTokens", 0), usage.get("outputTokens", 0)
        observability.record_usage(tin - self._seen_in, tout - self._seen_out)
        self._seen_in, self._seen_out = tin, tout

    def __call__(self, *args, **kwargs):
        try:
            return self._agent(*args, **kwargs)
        finally:
            self._report()

    def structured_output(self, output_model, prompt=None):
        # Route through the normal invocation (strands' non-deprecated path):
        # unlike Agent.structured_output, it runs the event loop, so token
        # usage lands in accumulated_usage and the cost trace sees it.
        try:
            result = self._agent(prompt, structured_output_model=output_model)
            if result.structured_output is None:
                raise RuntimeError(
                    f"model returned no structured output for {output_model.__name__}")
            return result.structured_output
        finally:
            self._report()


def make_agent(tier: str, system_prompt: str, tools: list | None = None):
    """Build a Strands agent on the given model tier ('haiku'|'sonnet'|'opus').

    callback_handler=None disables Strands' default token streaming to stdout —
    the generators run concurrently, so their streams would otherwise interleave
    into unreadable output. Saved artifacts are unaffected either way.
    """
    from strands import Agent

    return _TrackedAgent(Agent(
        model=_model(tier),
        system_prompt=system_prompt,
        tools=tools or [],
        callback_handler=None,
    ))
