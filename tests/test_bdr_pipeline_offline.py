"""Full bdr_outreach.yaml pipeline offline: the engine interprets the real
config end-to-end with Bedrock stubbed out. Proves the config drives the
product shape (stage graph, fan-outs, barrier, artifact layout, replay)
without model calls; real-Bedrock outcomes are the exit-criterion run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import poc2.stages  # noqa: F401
from poc2 import bedrock
from poc2.models import (
    AccountSummary, AccountValidation, ContactVerification, Enrichment,
    ResearchResult,
)
from poc2.pipeline.engine import Engine
from poc2.pipeline.schema import load_pipeline
from poc2.state import StateStore
from poc2.stages import persist as persist_mod
from poc2.storage import ArtifactStore

ROOT = Path(__file__).resolve().parent.parent

CANNED_ENRICHMENT = {
    "contacts": [
        {"name": "Elena Voss", "linkedin_found": True,
         "personalization_anchors": [
             {"type": "conference_talk", "quality": "strong",
              "source": "https://meridianrobotics.com/company/leadership"}]},
    ],
    "trigger_events": ["hiring_spike"],
    "warm_paths": [],
}


class FakeAgent:
    """Stands in for a Strands agent: deterministic canned outputs keyed on
    the structured-output model type (verification mimics the email-or-phone
    rule so the pool splits exactly like the real run)."""

    def __init__(self, tier, system):
        self.tier = tier

    def structured_output(self, model_cls, prompt):
        name = model_cls.__name__
        if name == "AccountValidation":
            return AccountValidation(status="VALID", reasons=["stub"])
        if name == "Enrichment":
            return Enrichment.model_validate(CANNED_ENRICHMENT)
        if name == "ContactVerification":
            data = json.loads(prompt.split(":\n", 1)[1])
            ok = bool(
                (data.get("email") and "@" in data["email"])
                or (data.get("phone") or "").startswith("+")
            )
            return ContactVerification(
                contact_id=data["contact_id"],
                status="VERIFIED" if ok else "INSUFFICIENT", reason="stub")
        if name == "ResearchResult":
            return ResearchResult(company_facts=["f1", "f2", "f3"], trigger_events=[])
        if name == "AccountSummary":
            return AccountSummary(bullets=["b1", "b2", "b3", "b4", "b5"])
        raise AssertionError(f"unexpected structured output: {name}")

    def __call__(self, prompt):
        return f"STUB-{self.tier}-CONTENT"


@pytest.fixture()
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(bedrock, "make_agent", lambda tier, system, tools=None: FakeAgent(tier, system))
    monkeypatch.setattr(
        persist_mod, "ArtifactStore",
        lambda: ArtifactStore(bucket="", local_dir=str(tmp_path)))
    return tmp_path


def _run(state=None):
    config = load_pipeline(ROOT / "pipelines" / "bdr_outreach.yaml", base_dir=ROOT)
    account = json.loads((ROOT / "mocks" / "sample_account.json").read_text())
    engine = Engine(config, state=state or StateStore(table=""))
    result = engine.run("batch-t1", account["account_id"], {"account": account})
    return engine, result


def test_full_pipeline_offline(offline):
    tmp_path = offline
    engine, result = _run()

    # identify lane: fixture pages -> canned enrichment -> verbatim prioritizer
    ident = result.outputs["prioritize"]
    names = [f"{c.first_name} {c.last_name}" for c in ident.contacts]
    assert names == ["Elena Voss", "Marcus Webb", "Divya Krishnan"]
    assert ident.email_pattern["confidence"] == "high"
    assert ident.incumbent_signals == ["trinet"]

    # verification: 4 CRM + 3 identified = 7 fanned checkpoints; junk fails
    verdicts = {v.contact_id: v.status for v in
                (ContactVerification.model_validate(o) for o in result.outputs["verify"])}
    assert len(verdicts) == 7
    assert verdicts["c-104"] == "INSUFFICIENT"  # Dead Lead
    assert verdicts["id-meridianrobo-3"] == "VERIFIED"  # pattern-guessed P3 survives

    # reconcile: alphabetical over the verified pool -> Alvarez, Berg, Krishnan
    assert [c["last_name"] for c in result.outputs["reconcile"]] == \
        ["Alvarez", "Berg", "Krishnan"]

    # generation: 3 contacts x 3 artifact types, all opus-tier stubs
    artifacts = result.outputs["generate"]
    assert len(artifacts) == 9
    assert {a.artifact_type for a in artifacts} == {"email", "linkedin", "talk_track"}

    # persist: poc1 §11.2 layout on disk
    manifest = result.outputs["persist"]
    assert manifest["artifact_count"] == 10
    prefix = tmp_path / "batch-t1" / "bdr-emea-07" / "acct-001"
    assert (prefix / "account_summary.json").exists()
    assert (prefix / "identified_contacts.json").exists()
    assert (prefix / "_manifest.json").exists()
    for cid in ("c-102", "c-103", "id-meridianrobo-3"):
        for kind in ("email", "linkedin", "talk_track"):
            assert (prefix / "contacts" / cid / f"{kind}.json").exists()

    # checkpoint accounting: 4 lane stages + 7 verify + reconcile + research
    # + summary + 9 generate = 23; persist stages are not checkpointed
    assert len(engine.state.computed) == 23
    assert engine.state.cached == []


def test_full_pipeline_replay_is_all_cached(offline):
    state = StateStore(table="")
    _run(state)
    assert len(state.computed) == 23

    calls = {"n": 0}
    real_make = bedrock.make_agent

    def counting_make(tier, system, tools=None):
        calls["n"] += 1
        return real_make(tier, system, tools)

    bedrock_make = bedrock.make_agent
    try:
        bedrock.make_agent = counting_make
        engine, result = _run(state)
    finally:
        bedrock.make_agent = bedrock_make

    # replay: every checkpointed stage served from state, zero agent builds
    assert len(state.computed) == 23          # unchanged
    assert len(state.cached) == 23
    assert calls["n"] == 0
    # persist still ran (checkpoint: false) and rebuilt the manifest
    assert result.outputs["persist"]["artifact_count"] == 10
