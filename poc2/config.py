"""Runtime configuration. Env vars override the defaults below.

Phase 0 keeps this minimal: region + state table (what StateStore needs) and
the artifact local dir the engine writes demo output under. Phase 1 extends it
with model tiers and the artifact bucket when bedrock.py / storage.py port over
(see docs/PORTING-GUIDE.md).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_HARDCODED = {"aws_region": "us-east-1"}


@lru_cache(maxsize=1)
def _json_cfg() -> dict:
    """Repo-root config.json, if present (local dev). The deployed runtime gets
    these as env vars from deploy.config, so it doesn't rely on the file."""
    path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _env(key: str, default: str) -> str:
    # Precedence: env var > config.json > hardcoded fallback.
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    aws_region: str = field(
        default_factory=lambda: _env(
            "AWS_REGION", _json_cfg().get("aws_region", _HARDCODED["aws_region"])
        )
    )

    # Idempotency/state table (poc1 §9). Empty -> in-memory (single-process)
    # fallback, so local runs need no DynamoDB. The AWS runtime sets this.
    state_ddb_table: str = field(default_factory=lambda: _env("STATE_DDB_TABLE", ""))

    # Local artifact/output dir for engine runs without S3.
    artifact_local_dir: str = field(default_factory=lambda: _env("ARTIFACT_LOCAL_DIR", "out"))


settings = Settings()
