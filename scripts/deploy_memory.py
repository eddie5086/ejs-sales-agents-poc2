#!/usr/bin/env python3
"""Create the AgentCore Memory store (Phase 4). Re-runnable.

    python scripts/deploy_memory.py

Short-term event storage only (no long-term extraction strategies): voice
exemplars and account history are read back verbatim, deterministically —
strategy-based semantic extraction can layer on later without a new store.
The memory ID is auto-discovered from the name; nothing lands in config.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bedrock_agentcore.memory import MemoryClient

from deploy import config as C


def main() -> int:
    client = MemoryClient(region_name=C.region())
    name = C.memory_name()
    print(f"AgentCore Memory store '{name}' ...")
    memory = client.create_or_get_memory(
        name=name,
        strategies=[],
        description="poc2 BDR voice exemplars + per-account event history",
        event_expiry_days=90,
    )
    mid = memory.get("id") or memory.get("memoryId")
    print(f"  memory id: {mid} (status {memory.get('status')})")
    print("\nDONE — memory:", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
