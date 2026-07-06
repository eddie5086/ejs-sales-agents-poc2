"""Offline checks for the deploy envelope pieces (no AWS calls)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_batch_envelope_asl_shape():
    asl = json.loads((ROOT / "poc2" / "orchestration" / "batch_envelope.asl.json").read_text())
    fan = asl["States"]["FanOutAccounts"]
    assert fan["Type"] == "Map"
    assert fan["MaxConcurrency"] == 3
    assert fan["ToleratedFailurePercentage"] == 50
    assert fan["ItemsPath"] == "$.accounts"
    # the Lambda ARN placeholder the deploy script substitutes
    task = fan["ItemProcessor"]["States"]["RunAccount"]
    assert task["Parameters"]["FunctionName"] == "${InvokeAccountFunctionArn}"
    assert task["Retry"][0]["MaxAttempts"] == 2


def test_lambda_handler_source_uses_runtime_arn_env():
    src = (ROOT / "lambda" / "invoke_account" / "handler.py").read_text()
    assert 'os.environ["RUNTIME_ARN"]' in src
    assert "invoke_agent_runtime" in src


def test_agentcore_app_defers_heavy_imports():
    """30s cold-start init cap (AWS-GOTCHAS §2): module level must not import
    the engine/strands stack; those imports live inside the handler."""
    src = (ROOT / "agentcore_app.py").read_text()
    module_level = [line for line in src.splitlines() if line.startswith(("import ", "from "))]
    assert not any("poc2" in line or "strands" in line for line in module_level)


def test_agentcore_requirements_cover_engine_deps():
    reqs = (ROOT / "agentcore-requirements.txt").read_text()
    for dep in ("bedrock-agentcore", "strands-agents", "pydantic", "boto3", "PyYAML"):
        assert dep in reqs, f"missing {dep}"
