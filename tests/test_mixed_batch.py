"""The mixed example batch (3 / 1 / 0 existing contacts) — offline checks
that each account exercises its designed path through identification and
can reach the 3-verified barrier."""
from __future__ import annotations

import json
from pathlib import Path

import poc2.stages  # noqa: F401
from poc2.models import Account
from poc2.pipeline.engine import StageContext
from poc2.pipeline.schema import StageConfig
from poc2.stages.prioritize import hris_committee

ROOT = Path(__file__).resolve().parent.parent
BATCH = json.loads((ROOT / "mocks" / "sample_batch_mixed.json").read_text())


def _account(account_id: str) -> dict:
    return next(a for a in BATCH["accounts"] if a["account_id"] == account_id)


def _identify(account: dict):
    pages = json.loads(
        (ROOT / "mocks" / "pages" / f"{account['domain']}.json").read_text())
    stage = StageConfig(id="prioritize", kind="policy", strategy="hris_committee",
                        params={"contacts_needed": 3,
                                "email_policy": "high_confidence_only"})
    ctx = StageContext(stage=stage, payload={"account": account},
                       outputs={"fetch_pages": pages})
    return hris_committee(ctx)


def test_batch_shape_and_fixtures():
    counts = {a["account_id"]: len(a["contacts"]) for a in BATCH["accounts"]}
    assert counts == {"acct-101": 3, "acct-102": 1, "acct-103": 0}
    for a in BATCH["accounts"]:
        Account.model_validate(a)  # schema-valid
        assert (ROOT / "mocks" / "pages" / f"{a['domain']}.json").exists()
    ids = [c["contact_id"] for a in BATCH["accounts"] for c in a["contacts"]]
    assert len(set(ids)) == len(ids)


def _reachable(contact) -> bool:
    return bool(contact.email or contact.phone)


def test_three_contact_account_pool_is_rich():
    """acct-101: 3 CRM + 3 identified; high-confidence pattern -> all
    identified get emails; the barrier is comfortably reachable."""
    account = _account("acct-101")
    ident = _identify(account)
    assert len(ident.contacts) == 3
    assert ident.email_pattern["confidence"] == "high"
    assert ident.incumbent_signals == ["trinet"]
    assert all(_reachable(c) for c in ident.contacts)
    crm_ok = [c for c in account["contacts"] if c["email"] or c["phone"]]
    assert len(crm_ok) + len(ident.contacts) == 6  # verify pool


def test_one_contact_account_needs_identified_to_reach_three():
    """acct-102: 1 CRM + identification; conflicting on-page mailboxes ->
    MODERATE pattern, so P3 (Head of IT) gets NO email — exactly 3 of the
    4-contact pool are reachable. The barrier passes with zero slack."""
    account = _account("acct-102")
    ident = _identify(account)
    assert ident.email_pattern["confidence"] == "moderate"
    reachable = [c for c in ident.contacts if _reachable(c)]
    assert len(reachable) == 2                      # P1 + P2 direct emails
    assert ident.contacts[2].email is None          # P3 withheld by policy
    assert 1 + len(reachable) == 3                  # CRM + identified = exactly 3


def test_zero_contact_account_is_fully_identification_driven():
    """acct-103: no CRM data at all — identification alone must produce 3
    reachable contacts (2 direct emails + 1 HIGH-confidence pattern guess)."""
    account = _account("acct-103")
    ident = _identify(account)
    assert len(ident.contacts) == 3
    assert ident.email_pattern["confidence"] == "high"
    assert all(c.email for c in ident.contacts)
    names = {f"{c.first_name} {c.last_name}" for c in ident.contacts}
    assert names == {"Nadia Petrova", "Samuel Obi", "Lena Fischer"}
    assert "Marco Bell" not in names  # the engineer is never prioritized
