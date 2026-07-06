"""AgentCore Memory accessors (Phase 4).

One memory store (name from deploy config, injected as MEMORY_NAME) holds:
  - per-BDR voice exemplars:      actor_id = "bdr/{bdr_id}",   session "voice"
  - per-account event history:    actor_id = "acct/{account_id}", session = batch_id

Contract: everything here is BEST-EFFORT and disabled when MEMORY_NAME is
empty (local runs) — voice callers fall back to the static snippet, event
appends become no-ops. Failures never take a pipeline down.

The memory ID (name + random suffix) is auto-discovered by matching the
configured name against list_memories — never stored in config (MIGRATION §4).
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from poc2.config import settings

VOICE_SESSION = "voice"


def _client():
    from bedrock_agentcore.memory import MemoryClient

    return MemoryClient(region_name=settings.aws_region)


@lru_cache(maxsize=1)
def memory_id() -> Optional[str]:
    """Resolve the store's id from its configured name; None = disabled."""
    if not settings.memory_name:
        return None
    try:
        for m in _client().list_memories():
            mid = m.get("id") or ""
            if mid.split("-")[0] == settings.memory_name or \
                    m.get("name") == settings.memory_name:
                return mid
    except Exception as e:
        print(f"  [memory] discovery failed ({type(e).__name__}: {e}); memory disabled")
    return None


def get_bdr_voice(bdr_id: str, max_exemplars: int = 5) -> Optional[str]:
    """The BDR's voice exemplars, newest last, joined for prompt use.
    None -> caller falls back to the static voice snippet."""
    mid = memory_id()
    if not mid:
        return None
    try:
        events = _client().list_events(
            memory_id=mid, actor_id=f"bdr/{bdr_id}", session_id=VOICE_SESSION,
            max_results=max_exemplars)
        texts: List[str] = []
        for ev in events:
            for msg in ev.get("payload") or []:
                text = (msg.get("conversational") or {}).get("content", {}).get("text")
                if text:
                    texts.append(text)
        if not texts:
            return None
        # list_events returns newest first; present oldest -> newest
        return "\n\n".join(reversed(texts))
    except Exception as e:
        print(f"  [memory] voice retrieval failed ({type(e).__name__}: {e}); using static")
        return None


def append_account_event(account_id: str, batch_id: str, text: str) -> bool:
    """Append one event to the account's history. Best-effort; False = skipped."""
    mid = memory_id()
    if not mid:
        return False
    try:
        _client().create_event(
            memory_id=mid, actor_id=f"acct/{account_id}", session_id=batch_id,
            messages=[(text, "ASSISTANT")])
        return True
    except Exception as e:
        print(f"  [memory] event append failed ({type(e).__name__}: {e}); skipped")
        return False


def account_history(account_id: str, batch_id: str, max_results: int = 20) -> List[str]:
    """The account's event texts for one batch (observability/debug helper)."""
    mid = memory_id()
    if not mid:
        return []
    out: List[str] = []
    for ev in _client().list_events(
            memory_id=mid, actor_id=f"acct/{account_id}", session_id=batch_id,
            max_results=max_results):
        for msg in ev.get("payload") or []:
            text = (msg.get("conversational") or {}).get("content", {}).get("text")
            if text:
                out.append(text)
    return out
