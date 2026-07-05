"""Contact Verification — Haiku, fanned out per contact (ported from poc1).

V1 qualification rule: VERIFIED iff valid email OR valid phone. The engine
fans this stage over the `contact_pool` items provider (CRM-provided contacts
+ identified candidates), checkpointing each as `verify#<contact_id>`.
"""
from __future__ import annotations

from poc2 import bedrock
from poc2.models import Contact, ContactIdentification, ContactVerification
from poc2.pipeline.registry import register, register_items


@register_items("contact_pool")
def contact_pool(payload: dict, outputs: dict) -> list[dict]:
    """Pool to verify = CRM-provided contacts + identified candidates,
    in that order (poc1 parity)."""
    crm = list(payload.get("account", {}).get("contacts") or [])
    identification = ContactIdentification.model_validate(outputs["prioritize"])
    return crm + [c.model_dump(mode="json") for c in identification.contacts]


@register("agent", "email_or_phone")
def verify_contact(ctx) -> ContactVerification:
    contact = Contact.model_validate(ctx.item)
    from poc2.stages.common import load_prompt

    agent = bedrock.make_agent(ctx.stage.tier, load_prompt(ctx.stage.prompt))
    prompt = f"Verify this contact:\n{contact.model_dump_json(indent=2)}"
    result = agent.structured_output(ContactVerification, prompt)
    # Trust our own id over the model's echo.
    result.contact_id = contact.contact_id
    return result
