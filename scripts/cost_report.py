#!/usr/bin/env python3
"""Cost-per-stage report from a run's persisted trace (Phase 5).

    python scripts/cost_report.py <batch_id> <bdr_id> <account_id>

Reads {batch}/{bdr}/{account}/_trace.json from the artifact bucket (or the
local out/ dir when no bucket is configured) and prints the aggregated
cost-per-stage table.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy import config as C
from poc2 import observability


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("batch_id")
    p.add_argument("bdr_id")
    p.add_argument("account_id")
    args = p.parse_args()

    key = f"{args.batch_id}/{args.bdr_id}/{args.account_id}/_trace.json"
    local = Path("out") / key
    if local.exists():
        doc = json.loads(local.read_text())
    else:
        import boto3

        s3 = boto3.client("s3", region_name=C.region())
        doc = json.loads(s3.get_object(Bucket=C.bucket(), Key=key)["Body"].read())

    print(f"cost per stage — {key}")
    print(observability.format_cost_table(observability.cost_table(doc["trace"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
