"""Contact Identification strategies (ported from poc1 contact_identification).

The `prioritize` child of the identify composite: maps the verbatim
hris_contact_prioritizer's P1-P3 onto our Contact model. The email-confidence
policy is encoded here and selected by config
(`email_policy: high_confidence_only`).
"""
from __future__ import annotations

import re
from typing import List, Optional

from poc2.lib.hris_contact_prioritizer import prioritize_contacts
from poc2.models import Contact, ContactIdentification, Enrichment, Source
from poc2.pipeline.registry import register
from poc2.stages.common import account_from

# Buyer personas for BambooHR (HR software for SMBs), highest-priority first.
# The placeholder uses these to shape plausible candidates.
_PERSONAS = [
    ("Chief People Officer", "Dana", "Okafor", "dana.okafor"),
    ("VP People", "Marco", "Bianchi", "marco.bianchi"),
    ("Head of Talent", "Aisha", "Rahman", "aisha.rahman"),
    ("HR Operations Manager", "Liam", "Novak", "liam.novak"),
    ("People Operations Lead", "Sara", "Lindqvist", "sara.lindqvist"),
]


def _domain(account) -> str:
    return (account.domain or "example.com").strip().lower()


def _slug(account) -> str:
    return re.sub(r"[^a-z0-9]", "", (account.name or "acct").lower())[:12] or "acct"


def _split_name(name: str) -> tuple[str, str]:
    parts = str(name).split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return (parts[0] if parts else "Unknown"), ""


def _usable_email(c: dict, policy: str) -> Optional[str]:
    """Email policy (user-approved in poc1, encoded from config): a direct
    address seen on a page always counts; a pattern-guessed address counts
    ONLY at high pattern confidence — lower-confidence guesses stay None so
    verification routes them to enrichment."""
    if policy != "high_confidence_only":
        raise ValueError(f"unknown email_policy in config: {policy!r}")
    direct = c.get("email_direct")
    if isinstance(direct, str) and direct:
        return direct
    guess = c.get("email_guess") or {}
    if guess.get("confidence") == "high":
        return guess.get("address")
    return None


@register("policy", "hris_committee")
def hris_committee(ctx) -> ContactIdentification:
    """Real identification: the handed-over prioritizer over fetched pages +
    enrichment judgment, P1-P3 mapped onto Contact.

    Note: the prioritizer ranks a fixed top-3 (P1-P3); contacts_needed > 3 is
    capped.
    """
    account = account_from(ctx.payload)
    need_count = int(ctx.params.get("contacts_needed", 3))
    email_policy = ctx.params.get("email_policy", "high_confidence_only")
    page_texts = ctx.outputs.get("fetch_pages") or []
    enrichment_out = ctx.outputs.get("enrich")
    enrichment = (
        Enrichment.model_validate(enrichment_out).model_dump()
        if enrichment_out is not None else None
    )

    result = prioritize_contacts(
        {
            "company_name": account.name,
            "domain": account.domain,
            "employee_estimate": account.size_band,  # int-or-band accepted
        },
        page_texts,
        enrichment,
    )
    company = _slug(account)
    contacts: List[Contact] = []
    for i, pc in enumerate(result["priority_contacts"][:max(0, need_count)]):
        first, last = _split_name(pc["name"])
        contacts.append(Contact(
            contact_id=f"id-{company}-{i + 1}",
            first_name=first,
            last_name=last,
            title=pc.get("title"),
            email=_usable_email(pc, email_policy),
            phone=pc.get("phone"),
            source=Source.IDENTIFICATION,
            priority=i + 1,
            identification_rationale=(
                f"{pc.get('role')} (weight {pc.get('role_weight')}), {pc['priority']} "
                f"in {result['size_bucket']} committee; account access "
                f"{result['access_score']}/{result['grade']}"),
            role=pc.get("role"),
            anchors=pc.get("personalization_anchors") or [],
        ))
    return ContactIdentification(
        account_id=account.account_id,
        strategy="hris_committee",
        contacts=contacts,
        access_score=result["access_score"],
        grade=result["grade"],
        size_bucket=result["size_bucket"],
        email_pattern=result["email_pattern"],
        incumbent_signals=result["incumbent_signals"],
        trigger_events=result["trigger_events"],
        warnings=result["warnings"],
    )


@register("policy", "placeholder")
def placeholder(ctx) -> ContactIdentification:
    """v0 stub — produces persona-based candidates with pattern-guessed emails
    so downstream lanes have something to work with (offline/demo runs)."""
    account = account_from(ctx.payload)
    need_count = int(ctx.params.get("contacts_needed", 3))
    domain = _domain(account)
    company = _slug(account)
    out: List[Contact] = []
    for i in range(max(0, need_count)):
        title, first, last, handle = _PERSONAS[i % len(_PERSONAS)]
        out.append(Contact(
            contact_id=f"id-{company}-{i + 1}",
            first_name=first,
            last_name=last,
            title=title,
            email=f"{handle}@{domain}",
            phone=None,
            source=Source.IDENTIFICATION,
            priority=i + 1,
            identification_rationale=f"{title} — persona-matched for BambooHR outreach (placeholder).",
        ))
    return ContactIdentification(
        account_id=account.account_id, strategy="placeholder", contacts=out)
