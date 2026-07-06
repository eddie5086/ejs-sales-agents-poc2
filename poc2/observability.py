"""Per-stage token/latency/cost tracing (Phase 5).

AgentCore Observability supplies the OTEL/X-Ray traces automatically (the
runtime image runs under opentelemetry-instrument). What poc1 lacked — and
this module adds — is the PER-STAGE cost story: the engine times every
checkpoint, the bedrock agent factory reports token usage into a per-thread
collector, and the run ends with a trace any caller can turn into a
cost-per-stage table.

Prices are USD per million tokens (input, output), current for the deployed
tiers as of 2026-07; update alongside model-tier changes in config.json.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Optional

PRICES_PER_MTOK = {
    "haiku": (1.00, 5.00),
    "sonnet": (3.00, 15.00),
    "opus": (15.00, 75.00),
}

_local = threading.local()


def reset_usage() -> None:
    _local.tokens_in = 0
    _local.tokens_out = 0


def record_usage(tokens_in: int, tokens_out: int) -> None:
    """Called by the agent factory's proxy after every model interaction."""
    _local.tokens_in = getattr(_local, "tokens_in", 0) + tokens_in
    _local.tokens_out = getattr(_local, "tokens_out", 0) + tokens_out


def take_usage() -> tuple[int, int]:
    usage = (getattr(_local, "tokens_in", 0), getattr(_local, "tokens_out", 0))
    reset_usage()
    return usage


def cost_usd(tier: Optional[str], tokens_in: int, tokens_out: int) -> float:
    if tier not in PRICES_PER_MTOK:
        return 0.0
    pin, pout = PRICES_PER_MTOK[tier]
    return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 6)


def cost_table(trace: list[dict]) -> list[dict]:
    """Aggregate a run trace by stage id (fan-out keys collapse to their
    stage: 'gen#c-102#email' -> 'gen'). One row per stage + a TOTAL row."""
    rows: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "cached": 0, "elapsed_ms": 0,
                 "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "tier": None})
    for entry in trace:
        stage = entry["key"].split("#")[0]
        row = rows[stage]
        row["calls"] += 1
        row["cached"] += 1 if entry["cached"] else 0
        row["elapsed_ms"] += entry["elapsed_ms"]
        row["tokens_in"] += entry["tokens_in"]
        row["tokens_out"] += entry["tokens_out"]
        row["cost_usd"] = round(row["cost_usd"] + entry["cost_usd"], 6)
        row["tier"] = row["tier"] or entry.get("tier")
    table = [{"stage": k, **v} for k, v in rows.items()]
    total = {"stage": "TOTAL",
             "calls": sum(r["calls"] for r in table),
             "cached": sum(r["cached"] for r in table),
             "elapsed_ms": sum(r["elapsed_ms"] for r in table),
             "tokens_in": sum(r["tokens_in"] for r in table),
             "tokens_out": sum(r["tokens_out"] for r in table),
             "cost_usd": round(sum(r["cost_usd"] for r in table), 6),
             "tier": None}
    return table + [total]


def format_cost_table(table: list[dict]) -> str:
    header = f"{'stage':<12} {'tier':<7} {'calls':>5} {'cached':>6} " \
             f"{'ms':>7} {'tok_in':>8} {'tok_out':>8} {'usd':>9}"
    lines = [header, "-" * len(header)]
    for r in table:
        lines.append(
            f"{r['stage']:<12} {str(r['tier'] or '-'):<7} {r['calls']:>5} "
            f"{r['cached']:>6} {r['elapsed_ms']:>7} {r['tokens_in']:>8} "
            f"{r['tokens_out']:>8} {r['cost_usd']:>9.4f}")
    return "\n".join(lines)
