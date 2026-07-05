"""Per-Account state + idempotency store (poc1 §9, ported verbatim).

Implements the write-once invariant: every stage transition is stored at
`(batch_id, account_id, stage)`. Re-running a stage whose result is already
present is a no-op — the stored output is returned instead of recomputing. This
is what makes weekly-batch replay safe and cheap: a replay skips the expensive
Opus generations (and every other completed stage) for Accounts already done.

Backend: DynamoDB when `STATE_DDB_TABLE` is set (the AWS runtime), else an
in-memory dict so local runs need no table (idempotency then holds within a
single process only).

Table schema (single table): pk = "{batch_id}#{account_id}", sk = stage id.
Stage items carry the stage's JSON output; an `sk="STATE"` item tracks the
Account's current named state for observability.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from poc2.config import settings


class StateStore:
    def __init__(self, table: Optional[str] = None, region: Optional[str] = None):
        self.table_name = table if table is not None else settings.state_ddb_table
        region = region or settings.aws_region
        if self.table_name:
            import boto3  # deferred: local in-memory runs must not need AWS

            self._ddb = boto3.client("dynamodb", region_name=region)
        else:
            self._ddb = None
        self._mem: dict[tuple[str, str], Any] = {}
        # Per-run bookkeeping so the manifest can report replay behavior.
        self.computed: list[str] = []
        self.cached: list[str] = []

    @property
    def backend(self) -> str:
        return f"dynamodb:{self.table_name}" if self.table_name else "memory"

    @staticmethod
    def _pk(batch_id: str, account_id: str) -> str:
        return f"{batch_id}#{account_id}"

    def _read(self, pk: str, sk: str) -> Optional[Any]:
        if self._ddb:
            item = self._ddb.get_item(
                TableName=self.table_name, Key={"pk": {"S": pk}, "sk": {"S": sk}}
            ).get("Item")
            return json.loads(item["output"]["S"]) if item and "output" in item else None
        return self._mem.get((pk, sk))

    def _write_once(self, pk: str, sk: str, payload: Any) -> None:
        if self._ddb:
            from botocore.exceptions import ClientError

            try:
                self._ddb.put_item(
                    TableName=self.table_name,
                    Item={
                        "pk": {"S": pk}, "sk": {"S": sk}, "stage": {"S": sk},
                        "output": {"S": json.dumps(payload)},
                        "ts": {"N": str(int(time.time()))},
                    },
                    # Write-once: only the first writer wins. A concurrent second
                    # writer failing the condition is fine — the value is present.
                    ConditionExpression="attribute_not_exists(pk)",
                )
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        else:
            self._mem.setdefault((pk, sk), payload)

    def checkpoint(
        self, batch_id: str, account_id: str, stage: str,
        compute: Callable[[], Any], loader: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        """Return the stored result for `stage` if present (no-op replay), else
        run `compute`, persist its output write-once, and return it.

        `compute` may return a pydantic model or a plain JSON-able value.
        `loader` reconstructs the model from stored JSON on a cache hit.
        """
        pk = self._pk(batch_id, account_id)
        existing = self._read(pk, stage)
        if existing is not None:
            self.cached.append(stage)
            return loader(existing) if loader else existing
        value = compute()
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        self._write_once(pk, stage, payload)
        self.computed.append(stage)
        return value

    def set_state(self, batch_id: str, account_id: str, state: str) -> None:
        """Record the Account's current named state (§9). Overwritten, not
        write-once — it's an observability marker, not the idempotency key."""
        pk = self._pk(batch_id, account_id)
        if self._ddb:
            self._ddb.put_item(
                TableName=self.table_name,
                Item={"pk": {"S": pk}, "sk": {"S": "STATE"}, "state": {"S": state},
                      "ts": {"N": str(int(time.time()))}},
            )
        else:
            self._mem[(pk, "STATE")] = {"state": state}
