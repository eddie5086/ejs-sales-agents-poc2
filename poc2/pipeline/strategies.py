"""Trivial built-in policy strategies (Phase 0 demo set).

These exist to prove the engine shape: registry resolution, params from
config, reading prior stage outputs, and determinism (no time/randomness/
network — replay must be byte-identical). Real product strategies arrive in
Phase 1 as ports of poc1's agent modules.
"""
from __future__ import annotations

from poc2.pipeline.engine import StageContext
from poc2.pipeline.registry import register, register_condition


@register("policy", "template")
def template(ctx: StageContext) -> str:
    """Render params['template'] with the run payload (e.g. 'Hello {name}')."""
    return ctx.params["template"].format(**ctx.payload)


@register("policy", "uppercase")
def uppercase(ctx: StageContext) -> str:
    """Uppercase the output of the stage named by params['source']."""
    return str(ctx.outputs[ctx.params["source"]]).upper()


@register("policy", "word_count")
def word_count(ctx: StageContext) -> int:
    """Count words in the output of the stage named by params['source']."""
    return len(str(ctx.outputs[ctx.params["source"]]).split())


@register_condition("always")
def always(outputs: dict) -> bool:
    return True
