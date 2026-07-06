"""Memory accessors + voice fallback — offline (MEMORY_NAME empty in tests,
so every path must short-circuit without AWS)."""
from __future__ import annotations

import poc2.stages  # noqa: F401
from poc2 import memory
from poc2.pipeline.engine import StageContext
from poc2.pipeline.schema import StageConfig
from poc2.stages.generate import _voice

ACCOUNT = {"account_id": "a1", "bdr_id": "bdr-emea-07", "name": "X"}


def _gen_ctx(params: dict) -> StageContext:
    stage = StageConfig(id="generate", kind="agent", tier="opus", generation=True,
                        strategy="artifact_generators", params=params)
    return StageContext(stage=stage, payload={"account": ACCOUNT}, outputs={})


def test_memory_disabled_without_name():
    memory.memory_id.cache_clear()
    assert memory.memory_id() is None            # settings.memory_name == ""
    assert memory.get_bdr_voice("bdr-emea-07") is None
    assert memory.append_account_event("a1", "b1", "x") is False
    assert memory.account_history("a1", "b1") == []


def test_voice_memory_falls_back_to_static_when_disabled():
    params = {"voice": "memory", "voice_prompt": "prompts/voice_static.md"}
    voice = _voice(_gen_ctx(params))
    assert "warm, direct, peer-to-peer" in voice  # the static snippet


def test_voice_memory_uses_exemplars_when_available(monkeypatch):
    monkeypatch.setattr(memory, "get_bdr_voice",
                        lambda bdr_id, max_exemplars=5: f"EXEMPLARS for {bdr_id}")
    params = {"voice": "memory", "voice_prompt": "prompts/voice_static.md"}
    voice = _voice(_gen_ctx(params))
    assert "EXEMPLARS for bdr-emea-07" in voice
    assert "match their style" in voice


def test_voice_static_never_touches_memory(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("voice: static must not hit memory")
    monkeypatch.setattr(memory, "get_bdr_voice", boom)
    params = {"voice": "static", "voice_prompt": "prompts/voice_static.md"}
    assert "warm, direct" in _voice(_gen_ctx(params))


def test_deploy_config_memory_name_derivation():
    from deploy import config as C
    assert C.memory_name() == "bdr_poc2_memory"     # hyphens -> underscores
    assert "-" not in C.memory_name()


def test_runtime_env_carries_memory_name(monkeypatch):
    from deploy import config as C
    monkeypatch.setattr(C, "account_id", lambda: "123456789012")
    assert C.runtime_env()["MEMORY_NAME"] == "bdr_poc2_memory"
    assert C.resolved()["memory_name"] == "bdr_poc2_memory"
