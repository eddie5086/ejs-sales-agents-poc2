"""Single source of truth for deploy-time configuration (ported from poc1).

Reads repo-root `config.json`, discovers the account id (STS), and derives every
resource name from `resource_prefix`. Everything account-specific flows from
here, so migrating to a new account = edit `config.json` + run `install.py`
(see MIGRATION.md).

Naming derivation (prefix `bdr-poc2`):
    S3 bucket        artifact_bucket override, else "{prefix}-artifacts-{account}"
    DynamoDB table   "{prefix}-state"
    Lambda           "{prefix}-invoke-account"      + role "-role"
    State machine    "{prefix}-batch-envelope"      + role "-role"
    AgentCore agent  agent_name (underscores; AgentCore names disallow hyphens)
    ECR repo         "bedrock-agentcore-{agent_name}"  (created by the toolkit)

Later phases extend this with the Memory store and Gateway names — any PR that
introduces an AWS resource extends this file and scripts/install.py in the
same PR.

Auto-discovered (never stored in config): the AgentCore runtime execution role
and runtime ARN — the toolkit mints these per account. Read from
`.bedrock_agentcore.yaml` after the runtime deploys.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
AGENTCORE_YAML = ROOT / ".bedrock_agentcore.yaml"

# Model-ID inference-profile prefixes are region-family-specific (MIGRATION §7).
REGION_FAMILY_PREFIX = {"us": "us.", "eu": "eu.", "ap": "apac."}


@functools.lru_cache(maxsize=1)
def _cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text())


# ---- Static config values ------------------------------------------------

def region() -> str:
    return _cfg()["aws_region"]


def prefix() -> str:
    return _cfg()["resource_prefix"]


def agent_name() -> str:
    return _cfg()["agent_name"]


def models() -> dict:
    return _cfg()["models"]


def validate() -> list[str]:
    """Static config lint (no AWS calls). Returns a list of problems."""
    problems = []
    p = prefix()
    if p != p.lower() or "_" in p:
        problems.append(f"resource_prefix '{p}' must be lowercase, hyphens only")
    if "-" in agent_name():
        problems.append(
            f"agent_name '{agent_name()}' must use underscores (AgentCore disallows hyphens)"
        )
    family = region().split("-")[0]
    expected = REGION_FAMILY_PREFIX.get(family)
    if expected:
        for tier, mid in models().items():
            if not mid.startswith(expected):
                problems.append(
                    f"model '{tier}' id '{mid}' does not match region family "
                    f"'{region()}' (expected prefix '{expected}')"
                )
    return problems


# ---- Discovered / derived ------------------------------------------------

@functools.lru_cache(maxsize=1)
def account_id() -> str:
    import boto3  # deferred: static derivations stay importable offline

    return boto3.client("sts", region_name=region()).get_caller_identity()["Account"]


def bucket() -> str:
    """Explicit override in config, else derived (must be globally unique)."""
    return _cfg().get("artifact_bucket") or f"{prefix()}-artifacts-{account_id()}"


def table() -> str:
    return f"{prefix()}-state"


def lambda_name() -> str:
    return f"{prefix()}-invoke-account"


def lambda_role() -> str:
    return f"{lambda_name()}-role"


def sfn_name() -> str:
    return f"{prefix()}-batch-envelope"


def sfn_role() -> str:
    return f"{sfn_name()}-role"


def ecr_repo() -> str:
    return f"bedrock-agentcore-{agent_name()}"


def memory_name() -> str:
    """AgentCore Memory store (Phase 4). Memory names disallow hyphens —
    same rule as agent names — so the prefix maps to underscores. The memory
    ID (name + random suffix) is auto-discovered by matching this name."""
    return f"{prefix().replace('-', '_')}_memory"


def runtime_env() -> dict:
    """Environment injected into the AgentCore runtime at deploy time."""
    m = models()
    return {
        "ARTIFACT_S3_BUCKET": bucket(),
        "STATE_DDB_TABLE": table(),
        "AWS_REGION": region(),
        "BEDROCK_MODEL_HAIKU": m["haiku"],
        "BEDROCK_MODEL_SONNET": m["sonnet"],
        "BEDROCK_MODEL_OPUS": m["opus"],
        "MEMORY_NAME": memory_name(),  # empty MEMORY_NAME = memory disabled
    }


# ---- Read the toolkit's post-deploy state --------------------------------

def _agentcore_agent() -> Optional[dict]:
    if not AGENTCORE_YAML.exists():
        return None
    import yaml

    cfg = yaml.safe_load(AGENTCORE_YAML.read_text())
    agents = cfg.get("agents") or {}
    if not agents:
        return None
    return agents[cfg.get("default_agent") or next(iter(agents))]


def runtime_arn() -> str:
    agent = _agentcore_agent()
    arn = (agent or {}).get("bedrock_agentcore", {}).get("agent_arn")
    if not arn:
        raise RuntimeError(
            "runtime ARN not found — run the AgentCore deploy first "
            "(it writes .bedrock_agentcore.yaml)."
        )
    return arn


def runtime_execution_role_name() -> str:
    """The toolkit-minted execution role (hash suffix differs per account)."""
    agent = _agentcore_agent()
    role_arn = (agent or {}).get("aws", {}).get("execution_role") or \
        (agent or {}).get("execution_role")
    if not role_arn:
        raise RuntimeError(
            "runtime execution role not found — run the AgentCore deploy first."
        )
    return role_arn.split("/")[-1]


def resolved() -> dict:
    """Everything resolved, for --print / verification (no ARN lookups that
    require a prior deploy)."""
    return {
        "aws_region": region(),
        "account_id": account_id(),
        "resource_prefix": prefix(),
        "artifact_bucket": bucket(),
        "dynamodb_table": table(),
        "agent_name": agent_name(),
        "ecr_repo": ecr_repo(),
        "lambda_name": lambda_name(),
        "lambda_role": lambda_role(),
        "state_machine": sfn_name(),
        "state_machine_role": sfn_role(),
        "memory_name": memory_name(),
        "models": models(),
    }


if __name__ == "__main__":
    problems = validate()
    if problems:
        for p in problems:
            print(f"CONFIG PROBLEM: {p}")
        raise SystemExit(1)
    print(json.dumps(resolved(), indent=2))
