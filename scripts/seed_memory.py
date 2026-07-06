#!/usr/bin/env python3
"""Seed two distinct BDR voices into the Memory store (Phase 4).

    python scripts/seed_memory.py

Idempotent-ish: exemplars are appended once per run; get_bdr_voice reads the
newest 5, so re-seeding refreshes rather than corrupts. The two voices are
deliberately far apart so the exit criterion ("measurably different artifact
voice from config+memory only") has something to measure.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bedrock_agentcore.memory import MemoryClient

from deploy import config as C

VOICES = {
    # The mock accounts' BDR: casual, punchy, short.
    "bdr-emea-07": [
        "Voice: casual and punchy. Sentences under 12 words. Contractions "
        "always. No corporate words (leverage, streamline, solution). Open "
        "with something I noticed about THEM, one line. Ask is tiny: 'worth "
        "a look?' / '15 min?'. Sign off with just my first name.",
        "Example of my email style:\n\nsubject: onboarding at 200 people\n\n"
        "hey — saw you're hiring across three countries. onboarding must be "
        "chaos right now. we fix exactly that. worth a look?\n— sam",
    ],
    # A second, deliberately contrasting voice: formal, metric-led.
    "bdr-na-04": [
        "Voice: formal and consultative. Full sentences, no contractions. "
        "Always cite one concrete metric or benchmark. Structure: context "
        "sentence, quantified pain, capability statement, meeting request "
        "with two proposed time windows. Close with 'Kind regards' and full "
        "name and title.",
        "Example of my email style:\n\nSubject: Reducing HR administration "
        "overhead at scale\n\nDear Ms. Chen, organizations between 200 and "
        "500 employees typically spend 14 hours per week on manual HR "
        "administration. BambooHR consolidates onboarding, PTO, and people "
        "data into a single system of record. Would Tuesday 10:00-10:30 or "
        "Thursday 14:00-14:30 suit you for a brief discussion? Kind regards, "
        "Jordan Ellis, Business Development Representative",
    ],
}


def main() -> int:
    client = MemoryClient(region_name=C.region())
    name = C.memory_name()
    mid = None
    for m in client.list_memories():
        if (m.get("id") or "").split("-")[0] == name or m.get("name") == name:
            mid = m["id"]
            break
    if not mid:
        print(f"memory '{name}' not found — run scripts/deploy_memory.py first")
        return 1

    for bdr_id, exemplars in VOICES.items():
        for text in exemplars:
            client.create_event(memory_id=mid, actor_id=f"bdr/{bdr_id}",
                                session_id="voice", messages=[(text, "ASSISTANT")])
        print(f"  seeded {len(exemplars)} exemplar(s) for {bdr_id}")
    print("\nDONE — voices seeded:", ", ".join(VOICES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
