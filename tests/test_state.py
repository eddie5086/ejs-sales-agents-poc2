"""Write-once checkpoint semantics (in-memory backend, zero AWS)."""
from poc2.state import StateStore


def test_checkpoint_computes_once_then_replays():
    store = StateStore(table="")
    calls = []

    def compute():
        calls.append(1)
        return {"value": 42}

    first = store.checkpoint("b1", "a1", "stage_x", compute)
    second = store.checkpoint("b1", "a1", "stage_x", compute)

    assert first == second == {"value": 42}
    assert len(calls) == 1  # replay is a no-op
    assert store.computed == ["stage_x"]
    assert store.cached == ["stage_x"]


def test_checkpoint_keys_isolated_by_batch_account_stage():
    store = StateStore(table="")
    store.checkpoint("b1", "a1", "s", lambda: "one")
    assert store.checkpoint("b2", "a1", "s", lambda: "two") == "two"
    assert store.checkpoint("b1", "a2", "s", lambda: "three") == "three"
    assert store.checkpoint("b1", "a1", "t", lambda: "four") == "four"
    assert store.checkpoint("b1", "a1", "s", lambda: "NEVER") == "one"


def test_loader_reconstructs_on_cache_hit():
    store = StateStore(table="")
    store.checkpoint("b", "a", "s", lambda: {"n": 1})
    out = store.checkpoint("b", "a", "s", lambda: None, loader=lambda d: d["n"])
    assert out == 1


def test_backend_reports_memory_without_table():
    assert StateStore(table="").backend == "memory"
