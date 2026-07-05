"""The pipeline engine: interprets a validated PipelineConfig.

Engine rules (docs/ARCHITECTURE.md):
- Each stage resolves (kind, strategy) in the registry of plain functions.
- Every stage execution is wrapped in `state.checkpoint(batch, account,
  stage_id)`; fan-out stages checkpoint per item (`verify#<contact_id>`).
  Write-once — replay is a no-op.
- `flow` declares ordering; `parallel` groups run threaded; `barrier` entries
  are declarative guards over the accumulated outputs.
- Composite stages run their declared children in order, each child
  checkpointed under its own id (the composite itself is not re-checkpointed —
  its output is the mapping of child outputs).
- Deterministic modules stay deterministic: nothing here injects time,
  randomness, or network into strategy execution.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

from poc2.pipeline import registry
from poc2.pipeline.schema import (
    FAN_OUT_SOURCES, Barrier, ParallelGroup, PipelineConfig, StageConfig,
)
from poc2.state import StateStore


class BarrierNotSatisfied(RuntimeError):
    """A declarative flow barrier failed; the pipeline halts for this account."""


@dataclass
class StageContext:
    """What a strategy sees: its stage config, the run payload, prior outputs,
    and — when fanned out — the single item this invocation covers."""
    stage: StageConfig
    payload: dict
    outputs: dict[str, Any]
    item: Optional[dict] = None

    @property
    def params(self) -> dict:
        return self.stage.params


@dataclass
class RunResult:
    pipeline: str
    batch_id: str
    account_id: str
    outputs: dict[str, Any] = field(default_factory=dict)
    computed: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, config: PipelineConfig, state: Optional[StateStore] = None):
        self.config = config
        self.state = state if state is not None else StateStore(table="")

    def run(self, batch_id: str, account_id: str, payload: Optional[dict] = None) -> RunResult:
        payload = payload or {}
        outputs: dict[str, Any] = {}
        for entry in self.config.flow:
            if isinstance(entry, str):
                outputs[entry] = self._run_stage(
                    self.config.stage(entry), batch_id, account_id, payload, outputs
                )
            elif isinstance(entry, ParallelGroup):
                with ThreadPoolExecutor(max_workers=len(entry.parallel)) as pool:
                    futures = {
                        sid: pool.submit(
                            self._run_stage,
                            self.config.stage(sid), batch_id, account_id, payload, outputs,
                        )
                        for sid in entry.parallel
                    }
                # Collected after the pool exits; a failure propagates here.
                for sid, fut in futures.items():
                    outputs[sid] = fut.result()
            elif isinstance(entry, Barrier):
                self._check_barrier(entry, outputs)
        return RunResult(
            pipeline=self.config.name, batch_id=batch_id, account_id=account_id,
            outputs=outputs, computed=list(self.state.computed),
            cached=list(self.state.cached),
        )

    # ---- stage execution ---------------------------------------------------

    def _run_stage(
        self, stage: StageConfig, batch_id: str, account_id: str,
        payload: dict, outputs: dict[str, Any],
    ) -> Any:
        if stage.kind == "composite":
            child_outputs: dict[str, Any] = {}
            for child_id in stage.stages or []:
                child_outputs[child_id] = self._run_stage(
                    self.config.stage(child_id), batch_id, account_id, payload, outputs
                )
                # children's outputs are visible to later children + downstream
                outputs[child_id] = child_outputs[child_id]
            return child_outputs

        if stage.fan_out:
            return self._run_fan_out(stage, batch_id, account_id, payload, outputs)

        fn = registry.resolve(stage.kind, stage.strategy)
        ctx = StageContext(stage=stage, payload=payload, outputs=outputs)
        return self.state.checkpoint(batch_id, account_id, stage.id, lambda: fn(ctx))

    def _run_fan_out(
        self, stage: StageConfig, batch_id: str, account_id: str,
        payload: dict, outputs: dict[str, Any],
    ) -> list[Any]:
        """Fan a stage over the payload items; each item checkpoints under
        `{stage_id}#{item_id}` so replay resumes per item."""
        fn = registry.resolve(stage.kind, stage.strategy)
        source_key = FAN_OUT_SOURCES[stage.fan_out]
        items = payload.get(source_key) or []
        results = []
        for idx, item in enumerate(items):
            item_id = item.get("id", str(idx)) if isinstance(item, dict) else str(idx)
            ctx = StageContext(stage=stage, payload=payload, outputs=outputs, item=item)
            results.append(
                self.state.checkpoint(
                    batch_id, account_id, f"{stage.id}#{item_id}", lambda c=ctx: fn(c)
                )
            )
        return results

    # ---- barriers ------------------------------------------------------------

    @staticmethod
    def _check_barrier(entry: Barrier, outputs: dict[str, Any]) -> None:
        failed = [
            name for name in entry.barrier.require
            if not registry.resolve_condition(name)(outputs)
        ]
        if failed:
            raise BarrierNotSatisfied(f"barrier conditions not met: {failed}")
