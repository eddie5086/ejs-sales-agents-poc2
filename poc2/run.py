"""Run a pipeline locally from its YAML config.

    python -m poc2.run pipelines/demo.yaml
    python -m poc2.run pipelines/bdr_outreach.yaml --account mocks/sample_account.json
    python -m poc2.run pipelines/bdr_outreach.yaml --batch mocks/sample_batch.json

Local-first: in-memory state store, artifacts under ./out. Agent stages make
real Bedrock calls (credentials required); the demo pipeline is policy-only
and needs no AWS. AWS-backed state/storage activate via env in Phase 2.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import poc2.pipeline.strategies  # noqa: F401 — demo built-ins
import poc2.stages  # noqa: F401 — product strategies populate the registry
from poc2.pipeline.engine import Engine
from poc2.pipeline.schema import ConfigError, load_pipeline
from poc2.state import StateStore


def _summarize(result) -> None:
    from poc2 import observability

    print(f"pipeline={result.pipeline} batch={result.batch_id} "
          f"account={result.account_id}")
    for stage_id, output in result.outputs.items():
        text = json.dumps(output, default=lambda o: getattr(o, "model_dump", lambda: str(o))())
        print(f"  {stage_id}: {text[:200]}{'…' if len(text) > 200 else ''}")
    print(f"computed={len(result.computed)} cached={len(result.cached)}")
    print(observability.format_cost_table(observability.cost_table(result.trace)))


def _run_account(config, account: dict, batch_id: str, overrides: dict | None = None):
    # Fresh store per account: manifest idempotency counts stay per-invocation
    # (poc1 semantics — in AWS each account is its own runtime invocation).
    payload = {"account": account, "_started_at": time.time(),
               "param_overrides": overrides or {}}
    engine = Engine(config, state=StateStore())
    result = engine.run(batch_id, account.get("account_id", "demo-account"), payload)
    _summarize(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pipeline", help="path to a pipeline YAML")
    parser.add_argument("--account", help="path to one account JSON")
    parser.add_argument("--batch", help="path to a batch JSON ({batch_id, accounts})")
    parser.add_argument("--batch-id", default="local-batch")
    parser.add_argument("--payload", default='{"company": "Acme"}',
                        help="JSON run payload (pipelines without --account/--batch)")
    parser.add_argument("--fixture-only", action="store_true",
                        help="pin the fetch chain to [attached, fixture] — "
                             "deterministic runs for fictional mock domains")
    args = parser.parse_args(argv)
    overrides = ({"fetch_pages": {"fetch": ["attached", "fixture"]}}
                 if args.fixture_only else {})

    try:
        config = load_pipeline(args.pipeline, base_dir=Path.cwd())
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 1

    if args.batch:
        batch = json.loads(Path(args.batch).read_text())
        batch_id = batch.get("batch_id", args.batch_id)
        for account in batch["accounts"]:
            print(f"\n=== Account {account['account_id']} ({account.get('name')}) ===")
            _run_account(config, account, batch_id, overrides)
        return 0

    if args.account:
        account = json.loads(Path(args.account).read_text())
        print(f"\n=== Account {account['account_id']} ({account.get('name')}) ===")
        _run_account(config, account, args.batch_id, overrides)
        return 0

    engine = Engine(config, state=StateStore())  # in-memory unless STATE_DDB_TABLE set
    result = engine.run(args.batch_id, "demo-account", json.loads(args.payload))
    print(f"state={engine.state.backend}")
    _summarize(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
