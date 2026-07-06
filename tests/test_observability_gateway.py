"""Phase 5 offline tests: cost tracing + gateway stage (zero AWS)."""
from __future__ import annotations

import pytest

import poc2.stages  # noqa: F401
from poc2 import gateway, observability
from poc2.pipeline.engine import Engine, StageContext
from poc2.pipeline.schema import PipelineConfig, StageConfig
from poc2.stages.crm_enrich import gateway_crm_lookup


def test_cost_math():
    assert observability.cost_usd("opus", 1_000_000, 0) == 15.0
    assert observability.cost_usd("haiku", 0, 1_000_000) == 5.0
    assert observability.cost_usd(None, 500, 500) == 0.0


def test_usage_collector_is_per_thread():
    observability.reset_usage()
    observability.record_usage(100, 20)
    observability.record_usage(50, 5)
    assert observability.take_usage() == (150, 25)
    assert observability.take_usage() == (0, 0)  # taken = cleared


def test_engine_emits_trace_and_cost_table():
    cfg = PipelineConfig.model_validate({
        "name": "t",
        "stages": [
            {"id": "greet", "kind": "policy", "strategy": "template",
             "params": {"template": "hi {company}"}},
            {"id": "shout", "kind": "policy", "strategy": "uppercase",
             "params": {"source": "greet"}},
        ],
        "flow": ["greet", "shout"],
    })
    result = Engine(cfg).run("b", "a", {"company": "Acme"})
    assert [e["key"] for e in result.trace] == ["greet", "shout"]
    assert all(e["cached"] is False and e["tokens_in"] == 0 for e in result.trace)

    table = observability.cost_table(result.trace)
    assert table[-1]["stage"] == "TOTAL"
    assert table[-1]["calls"] == 2
    # fan-out keys collapse by stage id
    fan = observability.cost_table([
        {"key": "gen#c1#email", "tier": "opus", "cached": False, "elapsed_ms": 5,
         "tokens_in": 10, "tokens_out": 20, "cost_usd": 0.01},
        {"key": "gen#c1#linkedin", "tier": "opus", "cached": False, "elapsed_ms": 5,
         "tokens_in": 10, "tokens_out": 20, "cost_usd": 0.01},
    ])
    assert fan[0]["stage"] == "gen" and fan[0]["calls"] == 2
    assert observability.format_cost_table(fan)  # renders


def test_tracked_agent_reports_usage():
    from poc2.bedrock import _TrackedAgent

    class FakeInner:
        def __init__(self):
            self.event_loop_metrics = type(
                "M", (), {"accumulated_usage": {"inputTokens": 0, "outputTokens": 0}})()

        def __call__(self, prompt, structured_output_model=None, **kwargs):
            if structured_output_model is None:
                self.event_loop_metrics.accumulated_usage = {
                    "inputTokens": 120, "outputTokens": 30}
                return "out"
            # structured path: usage accumulates on top of the prior call
            self.event_loop_metrics.accumulated_usage = {
                "inputTokens": 200, "outputTokens": 50}
            return type("R", (), {"structured_output": "structured"})()

    observability.reset_usage()
    agent = _TrackedAgent(FakeInner())
    assert agent("p") == "out"
    assert agent.structured_output(dict, "p") == "structured"
    assert observability.take_usage() == (200, 50)  # deltas, not double-counted


def _ctx(params=None, account=None):
    stage = StageConfig(id="crm_enrich", kind="tool", strategy="gateway_crm_lookup",
                        params=params or {})
    return StageContext(stage=stage, outputs={},
                        payload={"account": account or {
                            "account_id": "a", "bdr_id": "b",
                            "name": "Meridian Robotics",
                            "domain": "meridianrobotics.com"}})


def test_gateway_disabled_raises_clear_error():
    gateway.gateway_url.cache_clear()
    with pytest.raises(gateway.GatewayError, match="not configured"):
        gateway_crm_lookup(_ctx())


def test_crm_enrich_fills_only_missing_fields(monkeypatch):
    record = {"found": True, "domain": "meridianrobotics.com", "source": "crm",
              "industry": "Industrial Automation", "size_band": "201-500",
              "hq_region": "EMEA"}
    monkeypatch.setattr(gateway, "invoke_tool", lambda tool, args: record)
    # account already has industry -> only the truly missing fields fill
    out = gateway_crm_lookup(_ctx(account={
        "account_id": "a", "bdr_id": "b", "name": "Meridian Robotics",
        "domain": "meridianrobotics.com", "industry": "Robotics"}))
    assert out["found"] is True
    assert out["filled_fields"] == {"size_band": "201-500", "hq_region": "EMEA"}
    assert out["still_missing"] == []


def test_crm_enrich_unknown_domain(monkeypatch):
    monkeypatch.setattr(gateway, "invoke_tool",
                        lambda tool, args: {"found": False, "domain": args["domain"]})
    out = gateway_crm_lookup(_ctx(account={
        "account_id": "a", "bdr_id": "b", "name": "X", "domain": "nosuch.example"}))
    assert out["found"] is False
    assert out["filled_fields"] == {}
    assert set(out["still_missing"]) == {"industry", "size_band", "hq_region"}


def test_crm_lambda_handler_canned_data():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "crm_handler", Path(__file__).resolve().parent.parent
        / "lambda" / "crm_lookup" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    hit = mod.handler({"domain": "Meridianrobotics.com"}, None)
    assert hit["found"] and hit["size_band"] == "201-500"
    miss = mod.handler({"domain": "nosuch.example"}, None)
    assert miss["found"] is False
