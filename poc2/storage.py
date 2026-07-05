"""Terminal artifact store (poc1 §11.2, ported — key layout kept identical).

Writes the S3 layout from the handoff. If no bucket is configured, mirrors the
exact same key layout under a local directory so local runs need zero AWS
write setup. Same paths either way — nothing downstream cares which backend.

  {batch_id}/{bdr_id}/{account_id}/
      account_summary.json
      identified_contacts.json
      contacts/{contact_id}/{email,linkedin,talk_track}.json
      _manifest.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from poc2.config import settings


class ArtifactStore:
    def __init__(self, bucket: Optional[str] = None, local_dir: Optional[str] = None):
        self.bucket = bucket if bucket is not None else settings.artifact_s3_bucket
        self.local_dir = local_dir or settings.artifact_local_dir
        if self.bucket:
            import boto3  # deferred: local runs must not need AWS

            self._s3 = boto3.client("s3", region_name=settings.aws_region)
        else:
            self._s3 = None

    @property
    def backend(self) -> str:
        return f"s3://{self.bucket}" if self.bucket else f"local:{self.local_dir}"

    def put_json(self, key: str, payload: dict) -> str:
        body = json.dumps(payload, indent=2, default=str)
        if self._s3:
            self._s3.put_object(
                Bucket=self.bucket, Key=key, Body=body.encode(), ContentType="application/json"
            )
            return f"s3://{self.bucket}/{key}"
        path = Path(self.local_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return str(path)
