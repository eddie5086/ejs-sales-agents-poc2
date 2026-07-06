"""Runtime configuration. Env vars override the defaults below.

Model IDs are Bedrock inference-profile IDs, verified invocable on the dev
account (see docs/AWS-GOTCHAS.md §1). The deployed runtime gets everything as
env vars from deploy.config; local dev reads repo-root config.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_HARDCODED = {
    "aws_region": "us-east-1",
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "opus": "us.anthropic.claude-opus-4-6-v1",
}


@lru_cache(maxsize=1)
def _json_cfg() -> dict:
    """Repo-root config.json, if present (local dev). The deployed runtime gets
    these as env vars from deploy.config, so it doesn't rely on the file."""
    path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _default_region() -> str:
    return _json_cfg().get("aws_region", _HARDCODED["aws_region"])


def _default_model(tier: str) -> str:
    return _json_cfg().get("models", {}).get(tier, _HARDCODED[tier])


def _env(key: str, default: str) -> str:
    # Precedence: env var > config.json > hardcoded fallback.
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", _default_region()))

    # Tiered models (poc1 §4.7 discipline, now also linted by the pipeline schema).
    model_haiku: str = field(
        default_factory=lambda: _env("BEDROCK_MODEL_HAIKU", _default_model("haiku")))
    model_sonnet: str = field(
        default_factory=lambda: _env("BEDROCK_MODEL_SONNET", _default_model("sonnet")))
    model_opus: str = field(
        default_factory=lambda: _env("BEDROCK_MODEL_OPUS", _default_model("opus")))

    # Terminal artifact store. Empty bucket -> write to local dir instead.
    artifact_s3_bucket: str = field(default_factory=lambda: _env("ARTIFACT_S3_BUCKET", ""))
    artifact_local_dir: str = field(default_factory=lambda: _env("ARTIFACT_LOCAL_DIR", "out"))

    # Idempotency/state table. Empty -> in-memory (single-process) fallback,
    # so local runs need no DynamoDB. The AWS runtime sets this to a real table.
    state_ddb_table: str = field(default_factory=lambda: _env("STATE_DDB_TABLE", ""))

    # AgentCore Memory store name (Phase 4). Empty -> memory features off:
    # voice falls back to the static snippet, account events are skipped.
    # The AWS runtime sets this via deploy.config.runtime_env.
    memory_name: str = field(default_factory=lambda: _env("MEMORY_NAME", ""))

    def model_for_tier(self, tier: str) -> str:
        return {"haiku": self.model_haiku, "sonnet": self.model_sonnet, "opus": self.model_opus}[
            tier
        ]


settings = Settings()

# Deterministic vs. creative sampling by tier.
TIER_TEMPERATURE = {"haiku": 0.0, "sonnet": 0.3, "opus": 0.7}
