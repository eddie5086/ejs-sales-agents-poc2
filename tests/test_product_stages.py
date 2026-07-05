"""Offline tests for the ported product stages — no Bedrock calls (poc1
test_flow.py adapted to strategy shape). LLM stages are exercised against real
Bedrock via `python -m poc2.run pipelines/bdr_outreach.yaml` (exit criterion).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import poc2.stages  # noqa: F401 — populate registry
from poc2.models import Account, Contact, ContactIdentification, ContactVerification, Source
from poc2.pipeline.engine import StageContext
from poc2.pipeline.schema import StageConfig
from poc2.stages.fetch import fetch_pages
from poc2.stages.prioritize import hris_committee, placeholder
from poc2.stages.reconcile import ReconciliationError, alphabetical, priority_first
from poc2.storage import ArtifactStore

ROOT = Path(__file__).resolve().parent.parent


def _ctx(strategy: str, kind: str = "policy", params: dict | None = None,
         payload: dict | None = None, outputs: dict | None = None) -> StageContext:
    stage = StageConfig(id=strategy, kind=kind, strategy=strategy, params=params or {})
    return StageContext(stage=stage, payload=payload or {}, outputs=outputs or {},
                        batch_id="b-test", account_id="a-test")


def _contact(cid, first, last, email=None, phone=None, priority=None):
    return Contact(contact_id=cid, first_name=first, last_name=last,
                   email=email, phone=phone, priority=priority)


def _reconcile_ctx(pool, statuses, strategy="alphabetical", params=None):
    """Pool arrives via the contact_pool provider: CRM contacts + identified."""
    payload = {"account": {"account_id": "a", "bdr_id": "b", "name": "X",
                           "contacts": [c.model_dump(mode="json") for c in pool]}}
    outputs = {
        "prioritize": ContactIdentification(account_id="a", strategy="t", contacts=[]),
        "verify": [ContactVerification(contact_id=c.contact_id, status=statuses.get(c.contact_id, "VERIFIED"), reason="t")
                   for c in pool],
    }
    return _ctx(strategy, params=params or {"count": 3}, payload=payload, outputs=outputs)


def test_reconciliation_alphabetical_by_last_name():
    pool = [
        _contact("3", "Ann", "Wright", email="a@x.com"),
        _contact("1", "Bob", "Alvarez", email="b@x.com"),
        _contact("2", "Cy", "Nair", email="c@x.com"),
        _contact("4", "Di", "Berg", email="d@x.com"),
    ]
    picked = alphabetical(_reconcile_ctx(pool, {}))
    assert [c["last_name"] for c in picked] == ["Alvarez", "Berg", "Nair"]


def test_reconciliation_skips_unverified():
    pool = [
        _contact("1", "Bob", "Alvarez", email="b@x.com"),
        _contact("2", "Cy", "Nair", email="c@x.com"),
        _contact("3", "Ann", "Wright", email="a@x.com"),
        _contact("4", "Di", "Berg", email="d@x.com"),
    ]
    picked = alphabetical(_reconcile_ctx(pool, {"4": "INSUFFICIENT"}))
    assert [c["last_name"] for c in picked] == ["Alvarez", "Nair", "Wright"]


def test_reconciliation_too_few_raises():
    pool = [_contact("1", "A", "B", email="a@x.com")]
    with pytest.raises(ReconciliationError):
        alphabetical(_reconcile_ctx(pool, {}))


def test_reconciliation_priority_first_strategy():
    pool = [
        _contact("1", "Zed", "Zulu", email="z@x.com"),                  # CRM, no priority
        _contact("2", "Amy", "Aha", email="a@x.com", priority=2),
        _contact("3", "Bo", "Bee", email="b@x.com", priority=1),
        _contact("4", "Cat", "Cee", email="c@x.com", priority=3),
    ]
    picked = priority_first(_reconcile_ctx(pool, {}, strategy="priority_first"))
    assert [c["contact_id"] for c in picked] == ["3", "2", "4"]  # P1, P2, P3 before CRM


def test_contact_identification_placeholder():
    payload = {"account": {"account_id": "acct-9", "bdr_id": "bdr-1",
                           "name": "Meridian Robotics", "domain": "meridianrobotics.com"}}
    result = placeholder(_ctx("placeholder", params={"contacts_needed": 3}, payload=payload))
    assert result.account_id == "acct-9"
    assert result.strategy == "placeholder"
    assert len(result.contacts) == 3
    assert [c.priority for c in result.contacts] == [1, 2, 3]
    assert all(c.source == Source.IDENTIFICATION for c in result.contacts)
    assert all(c.email.endswith("@meridianrobotics.com") for c in result.contacts)


def _fixture_pages(domain="meridianrobotics.com"):
    return json.loads((ROOT / "mocks" / "pages" / f"{domain}.json").read_text())


def test_hris_committee_strategy_end_to_end_offline():
    """The real identification strategy over the sample fixture pages + a canned
    enrichment (what the Sonnet agent would return) — no Bedrock calls."""
    payload = {"account": {"account_id": "acct-9", "bdr_id": "bdr-1",
                           "name": "Meridian Robotics", "domain": "meridianrobotics.com",
                           "size_band": "201-500"}}
    enrichment = {
        "contacts": [
            {"name": "Elena Voss", "linkedin_found": True,
             "personalization_anchors": [
                 {"type": "conference_talk", "quality": "strong",
                  "source": "https://meridianrobotics.com/company/leadership"}]},
        ],
        "trigger_events": ["hiring_spike"],
        "warm_paths": [],
    }
    outputs = {"fetch_pages": _fixture_pages(), "enrich": enrichment}
    result = hris_committee(_ctx(
        "hris_committee",
        params={"contacts_needed": 3, "email_policy": "high_confidence_only"},
        payload=payload, outputs=outputs))
    assert result.strategy == "hris_committee"
    # 201-500 committee: champion (CPO) > economic buyer (CFO) > IT Director
    names = [f"{c.first_name} {c.last_name}" for c in result.contacts]
    assert names == ["Elena Voss", "Marcus Webb", "Divya Krishnan"]
    assert [c.priority for c in result.contacts] == [1, 2, 3]
    assert result.contacts[0].role == "champion_primary"
    # direct emails printed on the page attach; the IT Director gets a
    # high-confidence pattern guess (policy: high-confidence guesses count)
    assert result.contacts[0].email == "elena.voss@meridianrobotics.com"
    assert result.contacts[2].email == "divya.krishnan@meridianrobotics.com"
    assert result.email_pattern["confidence"] == "high"
    assert result.incumbent_signals == ["trinet"]
    assert result.access_score is not None and result.grade is not None
    # the engineer on the page is extracted but never prioritized
    assert "Jonas Feld" not in names


def test_hris_committee_low_confidence_guess_leaves_email_unset():
    """Email policy: without a high-confidence pattern, a guessed address must
    NOT be attached (verification then routes the contact to enrichment)."""
    page = ("Jane Doe — VP of People\nRaj Patel, Chief Financial Officer\n"
            "Divya Krishnan - IT Director\n"
            "Contact: jane.doe@acme.com or rpatel@acme.com")
    payload = {"account": {"account_id": "acct-9", "bdr_id": "bdr-1", "name": "Acme",
                           "domain": "acme.com", "size_band": "100-200"}}
    outputs = {"fetch_pages": [{"url": "page_texts[0]", "text": page}]}
    result = hris_committee(_ctx(
        "hris_committee",
        params={"contacts_needed": 3, "email_policy": "high_confidence_only"},
        payload=payload, outputs=outputs))
    by_name = {f"{c.first_name} {c.last_name}": c for c in result.contacts}
    assert by_name["Jane Doe"].email == "jane.doe@acme.com"   # direct, seen on page
    assert by_name["Raj Patel"].email == "rpatel@acme.com"    # also on the page -> direct
    assert by_name["Divya Krishnan"].email is None            # guess at moderate conf -> unset
    assert result.email_pattern["confidence"] == "moderate"


def test_northwind_fixture_parity_targets():
    """Northwind (51-200): moderate-confidence pattern -> P3 email withheld;
    Rippling incumbent -> warm paths contribute 0 (docs/PORTING-GUIDE.md)."""
    payload = {"account": {"account_id": "acct-002", "bdr_id": "bdr-1",
                           "name": "Northwind Logistics",
                           "domain": "northwindlogistics.com", "size_band": "51-200"}}
    outputs = {"fetch_pages": _fixture_pages("northwindlogistics.com")}
    result = hris_committee(_ctx(
        "hris_committee",
        params={"contacts_needed": 3, "email_policy": "high_confidence_only"},
        payload=payload, outputs=outputs))
    assert result.incumbent_signals == ["rippling"]
    assert result.email_pattern["confidence"] != "high"
    # P3 has no direct email and the pattern is not high-confidence -> withheld
    assert result.contacts[2].email is None


def test_fetch_pages_chain():
    def payload_for(account):
        return {"account": account}

    params = {"fetch": ["attached", "fixture"], "fixture_dir": "mocks/pages"}
    # fixture path (no attachment)
    acct = {"account_id": "a", "bdr_id": "b", "name": "Meridian Robotics",
            "domain": "meridianrobotics.com"}
    pages = fetch_pages(_ctx("fetch_pages", kind="tool", params=params, payload=payload_for(acct)))
    assert len(pages) == 2 and all(p["text"] for p in pages)
    # input-attached pages win over the fixture
    acct2 = {**acct, "page_texts": ["Jane Doe — VP of People"]}
    assert fetch_pages(_ctx("fetch_pages", kind="tool", params=params,
                            payload=payload_for(acct2))) == [
        {"url": "page_texts[0]", "text": "Jane Doe — VP of People"}]
    # unknown domain, no attachment -> empty (prioritizer degrades gracefully)
    acct3 = {"account_id": "a", "bdr_id": "b", "name": "X", "domain": "nosuch.example"}
    assert fetch_pages(_ctx("fetch_pages", kind="tool", params=params,
                            payload=payload_for(acct3))) == []
    # unknown source token in config is an error
    with pytest.raises(ValueError, match="unknown fetch source"):
        fetch_pages(_ctx("fetch_pages", kind="tool", params={"fetch": ["exa"]},
                         payload=payload_for(acct)))


def test_sample_account_parses_and_has_three_qualifiable():
    acct = Account.model_validate_json((ROOT / "mocks" / "sample_account.json").read_text())
    assert acct.account_id == "acct-001"
    # 3 contacts have a valid-looking email or phone; 1 is deliberately junk.
    plausible = [c for c in acct.contacts
                 if (c.email and "@" in c.email) or (c.phone and c.phone.startswith("+"))]
    assert len(plausible) == 3


def test_sample_batch_accounts_are_distinct_with_fixtures():
    batch = json.loads((ROOT / "mocks" / "sample_batch.json").read_text())
    accounts = [Account.model_validate(a) for a in batch["accounts"]]
    domains = [a.domain for a in accounts]
    assert len(set(domains)) == len(domains)
    contact_ids = [c.contact_id for a in accounts for c in a.contacts]
    assert len(set(contact_ids)) == len(contact_ids)
    for d in domains:
        assert (ROOT / "mocks" / "pages" / f"{d}.json").exists(), f"missing fixture for {d}"


def test_store_writes_layout_locally(tmp_path):
    store = ArtifactStore(bucket="", local_dir=str(tmp_path))
    uri = store.put_json("batch/bdr/acct/_manifest.json", {"ok": True})
    assert Path(uri).exists()
    assert json.loads(Path(uri).read_text())["ok"] is True
