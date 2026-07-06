"""Persist stages — ArtifactStore writes (poc1 §11.2 layout, identical keys).

Both strategies run with `checkpoint: false`: S3/local writes are idempotent
by key (architecture invariant 4), so they run on every replay — a wiped
output dir repopulates without touching the write-once stage state.
"""
from __future__ import annotations

import time

from poc2.models import AccountSummary, AccountValidation, Artifact, ContactIdentification
from poc2.pipeline.registry import register
from poc2.stages.common import account_from, artifact_prefix
from poc2.storage import ArtifactStore


@register("tool", "persist_identified")
def persist_identified(ctx) -> dict:
    """Close out identification: write identified_contacts.json (runs inside
    the identify composite, so it persists even if the account later fails
    the barrier — poc1 parity)."""
    identification = ContactIdentification.model_validate(ctx.outputs["prioritize"])
    store = ArtifactStore()
    uri = store.put_json(
        f"{artifact_prefix(ctx)}/identified_contacts.json", identification.model_dump())
    return {"uri": uri}


@register("tool", "artifact_writer")
def artifact_writer(ctx) -> dict:
    """Terminal writes: account summary, per-contact artifacts, manifest."""
    account = account_from(ctx.payload)
    validation = AccountValidation.model_validate(ctx.outputs["validate"])
    identification = ContactIdentification.model_validate(ctx.outputs["prioritize"])
    summary = AccountSummary.model_validate(ctx.outputs["summary"])
    artifacts = [Artifact.model_validate(a) for a in ctx.outputs["generate"]]
    selected_ids = [c["contact_id"] for c in ctx.outputs["reconcile"]]

    store = ArtifactStore()
    prefix = artifact_prefix(ctx)
    written = [store.put_json(
        f"{prefix}/account_summary.json",
        {"artifact_type": "account_summary", "account_id": account.account_id,
         "bullets": summary.bullets, "model_tier": "sonnet"},
    )]
    for art in artifacts:
        key = f"{prefix}/contacts/{art.contact_id}/{art.artifact_type}.json"
        written.append(store.put_json(key, art.model_dump()))

    manifest = {
        "batch_id": ctx.batch_id,
        "bdr_id": account.bdr_id,
        "account_id": account.account_id,
        "account_status": validation.status,
        "identified_contacts": {
            "strategy": identification.strategy,
            "count": len(identification.contacts),
            "access_score": identification.access_score,
            "grade": identification.grade,
        },
        "selected_contact_ids": selected_ids,
        "artifact_count": 1 + len(artifacts),
        "artifacts": written,
        "elapsed_seconds": round(
            time.time() - ctx.payload.get("_started_at", time.time()), 1),
        "state": "ARTIFACTS_QUEUED_REVIEW",  # terminal in v1
        "idempotency": {
            "backend": ctx.state.backend,
            "computed": len(ctx.state.computed),  # stages run this invocation
            "cached": len(ctx.state.cached),      # stages served from prior state
        },
        "pipeline": ctx.outputs.get("_pipeline_name", "bdr_outreach"),
        "deferred": ["hitl_gate_1", "hitl_gate_2", "enrichment_loopback",
                     "gateway_tools(phase5)", "guardrails"],
    }
    manifest_uri = store.put_json(f"{prefix}/_manifest.json", manifest)

    # Phase 4: append the account's event history (best-effort; no-op when
    # MEMORY_NAME is empty, e.g. every local run).
    from poc2 import memory
    event_logged = memory.append_account_event(
        account.account_id, ctx.batch_id,
        f"batch {ctx.batch_id} ran for {account.account_id} (bdr {account.bdr_id}): "
        f"{manifest['artifact_count']} artifacts queued for review, access "
        f"{identification.access_score}/{identification.grade}, "
        f"computed={manifest['idempotency']['computed']} "
        f"cached={manifest['idempotency']['cached']}")

    return {**manifest, "manifest_uri": manifest_uri, "store_backend": store.backend,
            "account_event_logged": event_logged}
