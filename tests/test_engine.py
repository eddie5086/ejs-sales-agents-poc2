"""Engine behavior: config-driven execution, write-once checkpointing,
parallel groups, composites, fan-out, barriers. All offline."""
import threading
import time

import pytest

import poc2.pipeline.strategies  # noqa: F401 — registers the built-ins
from poc2.pipeline import registry
from poc2.pipeline.engine import BarrierNotSatisfied, Engine, StageContext
from poc2.pipeline.registry import register, register_condition
from poc2.pipeline.schema import PipelineConfig
from poc2.state import StateStore


def _pipeline(raw: dict) -> PipelineConfig:
    return PipelineConfig.model_validate(raw)


# ---- test-only strategies -------------------------------------------------

_started = {}
_finished = {}


@register("policy", "_slow_marker")
def slow_marker(ctx: StageContext):
    sid = ctx.stage.id
    _started[sid] = time.monotonic()
    time.sleep(0.15)
    _finished[sid] = time.monotonic()
    return sid


@register("policy", "_thread_name")
def thread_name(ctx: StageContext):
    return threading.current_thread().name


@register("tool", "_echo_item")
def echo_item(ctx: StageContext):
    return {"echoed": ctx.item["id"]}


@register_condition("_greet_done")
def greet_done(outputs: dict) -> bool:
    return "greet" in outputs


@register_condition("_never")
def never(outputs: dict) -> bool:
    return False


# ---- tests ------------------------------------------------------------------

def test_demo_pipeline_runs_from_config():
    cfg = _pipeline({
        "name": "demo",
        "stages": [
            {"id": "greet", "kind": "policy", "strategy": "template",
             "params": {"template": "Hello {company}"}},
            {"id": "shout", "kind": "policy", "strategy": "uppercase",
             "params": {"source": "greet"}},
        ],
        "flow": ["greet", "shout"],
    })
    result = Engine(cfg).run("b", "a", {"company": "Acme"})
    assert result.outputs["greet"] == "Hello Acme"
    assert result.outputs["shout"] == "HELLO ACME"
    assert result.computed == ["greet", "shout"]
    assert result.cached == []


def test_every_stage_is_checkpointed_and_replay_is_noop():
    cfg = _pipeline({
        "name": "t",
        "stages": [
            {"id": "greet", "kind": "policy", "strategy": "template",
             "params": {"template": "hi {company}"}},
            {"id": "count", "kind": "policy", "strategy": "word_count",
             "params": {"source": "greet"}},
        ],
        "flow": ["greet", "count"],
    })
    store = StateStore(table="")
    Engine(cfg, state=store).run("b", "a", {"company": "Acme"})
    assert store.computed == ["greet", "count"]

    # Same store, same batch/account: full replay, nothing recomputed.
    replay = Engine(cfg, state=store).run("b", "a", {"company": "Acme"})
    assert replay.outputs["count"] == 2
    assert store.cached == ["greet", "count"]


def test_parallel_group_runs_stages_concurrently():
    _started.clear(); _finished.clear()
    cfg = _pipeline({
        "name": "t",
        "stages": [
            {"id": "p1", "kind": "policy", "strategy": "_slow_marker"},
            {"id": "p2", "kind": "policy", "strategy": "_slow_marker"},
        ],
        "flow": [{"parallel": ["p1", "p2"]}],
    })
    result = Engine(cfg).run("b", "a")
    assert result.outputs == {"p1": "p1", "p2": "p2"}
    # Concurrency: each stage started before the other finished.
    assert _started["p2"] < _finished["p1"]
    assert _started["p1"] < _finished["p2"]


def test_parallel_group_runs_off_main_thread():
    cfg = _pipeline({
        "name": "t",
        "stages": [{"id": "p1", "kind": "policy", "strategy": "_thread_name"}],
        "flow": [{"parallel": ["p1"]}],
    })
    result = Engine(cfg).run("b", "a")
    assert result.outputs["p1"] != "MainThread"


def test_composite_runs_children_in_order_each_checkpointed():
    cfg = _pipeline({
        "name": "t",
        "stages": [
            {"id": "greet", "kind": "policy", "strategy": "template",
             "params": {"template": "hi {company}"}},
            {"id": "shout", "kind": "policy", "strategy": "uppercase",
             "params": {"source": "greet"}},
            {"id": "combo", "kind": "composite", "stages": ["greet", "shout"]},
        ],
        "flow": ["combo"],
    })
    store = StateStore(table="")
    result = Engine(cfg, state=store).run("b", "a", {"company": "Acme"})
    assert result.outputs["combo"] == {"greet": "hi Acme", "shout": "HI ACME"}
    # children checkpoint under their own ids; the composite itself does not
    assert store.computed == ["greet", "shout"]


def test_fan_out_checkpoints_per_item():
    cfg = _pipeline({
        "name": "t",
        "stages": [{"id": "verify", "kind": "tool", "strategy": "_echo_item",
                    "fan_out": "per_contact"}],
        "flow": ["verify"],
    })
    store = StateStore(table="")
    payload = {"contacts": [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]}
    result = Engine(cfg, state=store).run("b", "a", payload)
    assert result.outputs["verify"] == [
        {"echoed": "c1"}, {"echoed": "c2"}, {"echoed": "c3"}]
    # fan-out runs threaded; completion order may vary, results stay ordered
    assert sorted(store.computed) == ["verify#c1", "verify#c2", "verify#c3"]

    # Replay: every item cached, none recomputed.
    Engine(cfg, state=store).run("b", "a", payload)
    assert sorted(store.cached) == ["verify#c1", "verify#c2", "verify#c3"]


def test_barrier_satisfied_lets_flow_continue():
    cfg = _pipeline({
        "name": "t",
        "stages": [
            {"id": "greet", "kind": "policy", "strategy": "template",
             "params": {"template": "hi"}},
            {"id": "shout", "kind": "policy", "strategy": "uppercase",
             "params": {"source": "greet"}},
        ],
        "flow": ["greet", {"barrier": {"require": ["_greet_done"]}}, "shout"],
    })
    result = Engine(cfg).run("b", "a")
    assert result.outputs["shout"] == "HI"


def test_barrier_unsatisfied_halts_pipeline():
    cfg = _pipeline({
        "name": "t",
        "stages": [
            {"id": "greet", "kind": "policy", "strategy": "template",
             "params": {"template": "hi"}},
            {"id": "shout", "kind": "policy", "strategy": "uppercase",
             "params": {"source": "greet"}},
        ],
        "flow": ["greet", {"barrier": {"require": ["_never"]}}, "shout"],
    })
    store = StateStore(table="")
    with pytest.raises(BarrierNotSatisfied, match="_never"):
        Engine(cfg, state=store).run("b", "a")
    assert store.computed == ["greet"]  # shout never ran


def test_unregistered_strategy_is_an_error():
    cfg = _pipeline({
        "name": "t",
        "stages": [{"id": "x", "kind": "policy", "strategy": "no_such_thing"}],
        "flow": ["x"],
    })
    with pytest.raises(registry.RegistryError, match="no_such_thing"):
        Engine(cfg).run("b", "a")
