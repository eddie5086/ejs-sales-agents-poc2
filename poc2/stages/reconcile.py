"""Contact Reconciliation — policy, no LLM (ported from poc1).

Normalizes the verified pool to exactly `count` contacts (3). The selection
strategy is the stage's config-declared strategy name — `alphabetical` is the
proven poc1 default; `priority_first` is offered as the obvious second.
"""
from __future__ import annotations

from typing import List

from poc2.models import Contact, ContactVerification
from poc2.pipeline.registry import register, register_items, resolve_items


class ReconciliationError(RuntimeError):
    """Raised when the verified pool cannot be normalized to 3 (too few)."""


def _verified_pool(ctx) -> List[Contact]:
    pool = [Contact.model_validate(c)
            for c in resolve_items("contact_pool")(ctx.payload, ctx.outputs)]
    verdicts = {
        v.contact_id: v.status
        for v in (ContactVerification.model_validate(o) for o in ctx.outputs["verify"])
    }
    return [c for c in pool if verdicts.get(c.contact_id) == "VERIFIED"]


def _select(ctx, order_key) -> List[dict]:
    count = int(ctx.params.get("count", 3))
    verified = _verified_pool(ctx)
    if len(verified) < count:
        raise ReconciliationError(
            f"only {len(verified)} verified contact(s); need {count}. "
            "Contact Identification loop-back is deferred (parity with poc1)."
        )
    ordered = sorted(verified, key=order_key)
    return [c.model_dump(mode="json") for c in ordered[:count]]


@register("policy", "alphabetical")
def alphabetical(ctx) -> List[dict]:
    return _select(ctx, lambda c: (c.last_name.lower(), c.first_name.lower()))


@register("policy", "priority_first")
def priority_first(ctx) -> List[dict]:
    """Identified priority (1 = highest) first, None (CRM contacts) last;
    alphabetical tiebreak keeps it deterministic."""
    return _select(ctx, lambda c: (c.priority is None, c.priority or 0,
                                   c.last_name.lower(), c.first_name.lower()))


@register_items("selected_contacts")
def selected_contacts(payload: dict, outputs: dict) -> list[dict]:
    """The reconciled selection — what generation fans over."""
    return list(outputs["reconcile"])
