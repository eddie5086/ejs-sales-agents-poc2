#!/usr/bin/env python3
"""Start a Step Functions batch execution and wait for the results.

    python scripts/run_batch.py [path/to/batch.json] --batch-id batch-xyz

Re-running the SAME batch_id is the replay path (all stages served from
DynamoDB, ~0 model calls); use a FRESH batch_id to exercise new code
(docs/AWS-GOTCHAS.md §3). Prints each account's manifest summary + timing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from deploy import config as C


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("batch", nargs="?", default="mocks/sample_batch.json")
    p.add_argument("--batch-id", required=True,
                   help="fresh id = cold run; repeated id = replay")
    p.add_argument("--fixture-only", action="store_true",
                   help="pin the fetch chain to [attached, fixture] — deterministic "
                        "parity runs for the fictional mock accounts (their parked "
                        "domains would otherwise feed the browser garbage)")
    args = p.parse_args()

    batch = json.loads(Path(args.batch).read_text())
    # param_overrides is always present — the ASL ItemSelector references it.
    payload = {"batch_id": args.batch_id, "accounts": batch["accounts"],
               "param_overrides": {}}
    if args.fixture_only:
        payload["param_overrides"] = {"fetch_pages": {"fetch": ["attached", "fixture"]}}

    sfn = boto3.client("stepfunctions", region_name=C.region())
    sm_arn = f"arn:aws:states:{C.region()}:{C.account_id()}:stateMachine:{C.sfn_name()}"
    t0 = time.time()
    exe = sfn.start_execution(
        stateMachineArn=sm_arn,
        name=f"{args.batch_id}-{int(t0)}",
        input=json.dumps(payload),
    )["executionArn"]
    print(f"execution: {exe}")

    while True:
        desc = sfn.describe_execution(executionArn=exe)
        if desc["status"] != "RUNNING":
            break
        time.sleep(2)
    wall = round(time.time() - t0, 1)
    print(f"status={desc['status']} wall={wall}s")
    if desc["status"] != "SUCCEEDED":
        print(desc.get("cause") or desc.get("error") or "")
        return 1

    results = json.loads(desc["output"])["results"]
    for m in results:
        idem = m.get("idempotency", {})
        print(f"  {m['account_id']}: {m.get('elapsed_seconds')}s "
              f"computed={idem.get('computed')} cached={idem.get('cached')} "
              f"artifacts={m.get('artifact_count')} "
              f"access={m.get('identified_contacts', {}).get('access_score')}/"
              f"{m.get('identified_contacts', {}).get('grade')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
