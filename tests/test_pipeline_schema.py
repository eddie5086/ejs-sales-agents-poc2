"""Config schema lints — every failure mode is a startup error."""
import pytest

from poc2.pipeline.schema import ConfigError, PipelineConfig, load_pipeline

VALID = {
    "name": "t",
    "stages": [
        {"id": "a", "kind": "policy", "strategy": "template", "params": {"template": "x"}},
        {"id": "b", "kind": "policy", "strategy": "uppercase", "params": {"source": "a"}},
    ],
    "flow": ["a", "b"],
}


def _cfg(**overrides):
    raw = {**VALID, **overrides}
    return PipelineConfig.model_validate(raw)


def test_valid_config_parses():
    cfg = _cfg()
    assert [s.id for s in cfg.stages] == ["a", "b"]


def test_duplicate_stage_ids_rejected():
    stages = VALID["stages"] + [
        {"id": "a", "kind": "policy", "strategy": "template", "params": {"template": "y"}}
    ]
    with pytest.raises(ValueError, match="duplicate stage ids"):
        _cfg(stages=stages)


def test_dangling_flow_ref_rejected():
    with pytest.raises(ValueError, match="undeclared stage 'zzz'"):
        _cfg(flow=["a", "zzz"])


def test_dangling_parallel_member_rejected():
    with pytest.raises(ValueError, match="undeclared stage 'zzz'"):
        _cfg(flow=[{"parallel": ["a", "zzz"]}])


def test_tier_lint_opus_requires_generation():
    stages = VALID["stages"] + [
        {"id": "g", "kind": "agent", "tier": "opus", "strategy": "s"}
    ]
    with pytest.raises(ValueError, match="tier lint"):
        _cfg(stages=stages, flow=["a", "b", "g"])


def test_opus_with_generation_flag_allowed():
    stages = VALID["stages"] + [
        {"id": "g", "kind": "agent", "tier": "opus", "strategy": "s", "generation": True}
    ]
    cfg = _cfg(stages=stages, flow=["a", "b", "g"])
    assert cfg.stage("g").generation is True


def test_agent_stage_requires_tier():
    stages = [{"id": "a", "kind": "agent", "strategy": "s"}]
    with pytest.raises(ValueError, match="requires a tier"):
        _cfg(stages=stages, flow=["a"])


def test_fan_out_rejected_on_policy_kind():
    stages = [{"id": "a", "kind": "policy", "strategy": "s", "fan_out": "per_contact"}]
    with pytest.raises(ValueError, match="fan_out is only valid"):
        _cfg(stages=stages, flow=["a"])


def test_composite_requires_children_and_no_strategy():
    with pytest.raises(ValueError, match="must list child stages"):
        _cfg(stages=[{"id": "c", "kind": "composite"}], flow=["c"])
    with pytest.raises(ValueError, match="cannot have a strategy"):
        _cfg(stages=[{"id": "c", "kind": "composite", "strategy": "s", "stages": ["a"]}],
             flow=["c"])


def test_composite_children_must_be_declared():
    stages = VALID["stages"] + [{"id": "c", "kind": "composite", "stages": ["nope"]}]
    with pytest.raises(ValueError, match="undeclared stage 'nope'"):
        _cfg(stages=stages, flow=["c"])


def test_barrier_requires_conditions():
    with pytest.raises(ValueError, match="at least one condition"):
        _cfg(flow=["a", {"barrier": {"require": []}}, "b"])


def test_missing_prompt_file_rejected(tmp_path):
    yaml_file = tmp_path / "p.yaml"
    yaml_file.write_text(
        "name: t\n"
        "stages:\n"
        "  - {id: a, kind: agent, tier: haiku, strategy: s, prompt: prompts/nope.md}\n"
        "flow: [a]\n"
    )
    with pytest.raises(ConfigError, match="missing prompt files"):
        load_pipeline(yaml_file, base_dir=tmp_path)


def test_prompt_file_present_accepted(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "ok.md").write_text("prompt body")
    yaml_file = tmp_path / "p.yaml"
    yaml_file.write_text(
        "name: t\n"
        "stages:\n"
        "  - {id: a, kind: agent, tier: haiku, strategy: s, prompt: prompts/ok.md}\n"
        "flow: [a]\n"
    )
    cfg = load_pipeline(yaml_file, base_dir=tmp_path)
    assert cfg.stage("a").prompt == "prompts/ok.md"


def test_missing_pipeline_file_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_pipeline(tmp_path / "absent.yaml")
