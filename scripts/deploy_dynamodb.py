#!/usr/bin/env python3
"""Create the DynamoDB state table (§9). Re-runnable.

    python scripts/deploy_dynamodb.py

The runtime execution role is granted access to this table by
scripts/deploy_agentcore.py (which knows the toolkit-minted role name), so this
script is order-independent — create the table before or after the runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from deploy import config as C


def main() -> int:
    ddb = boto3.client("dynamodb", region_name=C.region())
    table = C.table()
    print("DynamoDB state table ...")
    try:
        ddb.create_table(
            TableName=table,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"  creating table {table} ...")
        ddb.get_waiter("table_exists").wait(TableName=table)
        print("  table ACTIVE")
    except ddb.exceptions.ResourceInUseException:
        print(f"  table {table} already exists")
    print("\nDONE — table:", table)
    print("(runtime role is granted access by scripts/deploy_agentcore.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
