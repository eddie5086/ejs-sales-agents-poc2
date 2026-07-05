"""Generator stage — Opus, fanned out per contact × artifact type (ported
from poc1's three generator agents).

The engine expands `fan_out: per_contact` × `artifacts: [email, linkedin,
talk_track]` into 9 checkpointed jobs (`gen#<contact_id>#<artifact>`); this
strategy handles ONE (contact, artifact) pair per invocation. Prompts live in
per-artifact files (system prompt above the `---` line, instruction below).

Voice: `voice: static` reads the baseline snippet file (poc1's inline
BDR_VOICE_BASELINE, externalized). AgentCore Memory (`voice: memory`) lands in
Phase 4 behind the same param.
"""
from __future__ import annotations

from poc2 import bedrock
from poc2.models import AccountSummary, Artifact, Contact, ResearchResult, Source
from poc2.pipeline.registry import register
from poc2.stages.common import account_from, load_prompt, load_prompt_sections


def _voice(ctx) -> str:
    mode = ctx.params.get("voice", "static")
    if mode != "static":
        raise ValueError(f"voice mode {mode!r} not available until Phase 4")
    return load_prompt(ctx.params["voice_prompt"])


@register("agent", "artifact_generators")
def generate_artifact(ctx) -> Artifact:
    account = account_from(ctx.payload)
    contact = Contact.model_validate(ctx.item)
    summary = AccountSummary.model_validate(ctx.outputs["summary"])
    research = ResearchResult.model_validate(ctx.outputs["research"])

    system, instruction = load_prompt_sections(ctx.params["prompts"][ctx.artifact])
    product = load_prompt(ctx.params["product_prompt"])

    shared_context = (
        f"{product}\n\n{_voice(ctx)}\n\n"
        f"ACCOUNT SUMMARY (5 bullets):\n- " + "\n- ".join(summary.bullets) + "\n\n"
        f"RESEARCH:\n{research.model_dump_json(indent=2)}\n\n"
        f"CONTACT:\n{contact.model_dump_json(indent=2)}"
    )
    agent = bedrock.make_agent(ctx.stage.tier, system)
    content = str(agent(f"{shared_context}\n\n{instruction}")).strip()
    return Artifact(
        artifact_type=ctx.artifact,
        account_id=account.account_id,
        contact_id=contact.contact_id,
        content=content,
        model_tier=ctx.stage.tier,
        source=Source.RESEARCH,
    )
