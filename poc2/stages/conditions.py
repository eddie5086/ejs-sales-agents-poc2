"""Barrier conditions for the bdr_outreach flow (poc1's sync barrier, §8
Stage 5, now declarative config)."""
from __future__ import annotations

from poc2.models import AccountValidation, ContactVerification
from poc2.pipeline.registry import register_condition


@register_condition("account_valid")
def account_valid(outputs: dict) -> bool:
    return AccountValidation.model_validate(outputs["validate"]).status == "VALID"


@register_condition("three_verified")
def three_verified(outputs: dict) -> bool:
    selected = outputs.get("reconcile") or []
    verdicts = {
        v.contact_id: v.status
        for v in (ContactVerification.model_validate(o) for o in outputs.get("verify") or [])
    }
    return len(selected) == 3 and all(
        verdicts.get(c["contact_id"]) == "VERIFIED" for c in selected
    )
