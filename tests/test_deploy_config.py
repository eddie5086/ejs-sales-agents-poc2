"""deploy.config derivation rules — offline (STS is monkeypatched)."""
import pytest

from deploy import config as C


@pytest.fixture(autouse=True)
def fake_account(monkeypatch):
    monkeypatch.setattr(C, "account_id", lambda: "123456789012")


def test_every_name_derives_from_prefix():
    r = C.resolved()
    p = C.prefix()
    assert r["dynamodb_table"] == f"{p}-state"
    assert r["lambda_name"] == f"{p}-invoke-account"
    assert r["lambda_role"] == f"{p}-invoke-account-role"
    assert r["state_machine"] == f"{p}-batch-envelope"
    assert r["state_machine_role"] == f"{p}-batch-envelope-role"
    assert r["ecr_repo"] == f"bedrock-agentcore-{C.agent_name()}"


def test_bucket_auto_derives_with_account_id():
    # config.json ships artifact_bucket: "" -> derived, globally unique
    assert C.bucket() == f"{C.prefix()}-artifacts-123456789012"


def test_bucket_explicit_override_wins(monkeypatch):
    base = {**C._cfg(), "artifact_bucket": "my-bucket"}
    monkeypatch.setattr(C, "_cfg", lambda: base)
    assert C.bucket() == "my-bucket"


def test_agent_name_has_no_hyphens():
    assert "-" not in C.agent_name()  # AgentCore naming rule


def test_config_lint_passes_on_shipped_config():
    assert C.validate() == []


def test_config_lint_catches_region_model_mismatch(monkeypatch):
    bad = {**C._cfg(), "aws_region": "eu-west-1"}
    monkeypatch.setattr(C, "_cfg", lambda: bad)
    problems = C.validate()
    assert any("region family" in p for p in problems)


def test_config_lint_catches_hyphenated_agent_name(monkeypatch):
    bad = {**C._cfg(), "agent_name": "bad-name"}
    monkeypatch.setattr(C, "_cfg", lambda: bad)
    assert any("underscores" in p for p in C.validate())


def test_runtime_env_carries_all_models():
    env = C.runtime_env()
    assert env["STATE_DDB_TABLE"] == C.table()
    assert env["ARTIFACT_S3_BUCKET"] == C.bucket()
    for tier in ("HAIKU", "SONNET", "OPUS"):
        assert env[f"BEDROCK_MODEL_{tier}"]
