"""Declarative pipeline config schema (docs/ARCHITECTURE.md).

One versioned YAML defines every stage; the engine interprets it. All config
lint failures are STARTUP errors, raised here at load/validate time:

  - duplicate stage ids
  - dangling flow references (including parallel-group members)
  - composite children that reference undeclared stages
  - missing prompt files
  - tier lint: `opus` on a stage not flagged `generation: true`
  - fan_out on non-fannable kinds (only agent/tool stages fan out)
  - agent stages without a tier

Phase-0 calls where the docs are silent (recorded in CLAUDE.md): composite
children are top-level declared stages referenced by id; fannable kinds are
{agent, tool}; barrier `require` names resolve in the engine's condition
registry at runtime.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StageKind = Literal["policy", "agent", "tool", "composite"]
ModelTier = Literal["haiku", "sonnet", "opus"]

FANNABLE_KINDS = {"agent", "tool"}
# fan_out mode -> key in the run payload holding the items to fan over.
FAN_OUT_SOURCES = {"per_contact": "contacts", "per_item": "items"}


class ConfigError(ValueError):
    """A pipeline config failed validation. Always a startup error."""


class StageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: StageKind
    tier: Optional[ModelTier] = None
    strategy: Optional[str] = None
    prompt: Optional[str] = None
    params: dict = Field(default_factory=dict)
    generation: bool = False
    fan_out: Optional[Literal["per_contact", "per_item"]] = None
    stages: Optional[list[str]] = None  # composite children (declared stage ids)
    artifacts: Optional[list[str]] = None

    @model_validator(mode="after")
    def _lint_stage(self) -> "StageConfig":
        if self.kind == "composite":
            if not self.stages:
                raise ValueError(f"composite stage '{self.id}' must list child stages")
            if self.strategy:
                raise ValueError(f"composite stage '{self.id}' cannot have a strategy")
        else:
            if not self.strategy:
                raise ValueError(f"stage '{self.id}' ({self.kind}) requires a strategy")
            if self.stages:
                raise ValueError(f"only composite stages may list child stages ('{self.id}')")
        if self.kind == "agent" and self.tier is None:
            raise ValueError(f"agent stage '{self.id}' requires a tier")
        # Tier discipline: Opus never creeps into non-generation stages.
        if self.tier == "opus" and not self.generation:
            raise ValueError(
                f"stage '{self.id}': tier 'opus' requires 'generation: true' (tier lint)"
            )
        if self.fan_out and self.kind not in FANNABLE_KINDS:
            raise ValueError(
                f"stage '{self.id}': fan_out is only valid on kinds {sorted(FANNABLE_KINDS)}"
            )
        return self


class ParallelGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parallel: list[str]

    @field_validator("parallel")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("parallel group must list at least one stage id")
        return v


class BarrierSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require: list[str]

    @field_validator("require")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("barrier must require at least one condition")
        return v


class Barrier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barrier: BarrierSpec


FlowEntry = Union[str, ParallelGroup, Barrier]


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    stages: list[StageConfig]
    flow: list[FlowEntry]

    @model_validator(mode="after")
    def _lint_pipeline(self) -> "PipelineConfig":
        ids = [s.id for s in self.stages]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate stage ids: {sorted(dupes)}")
        declared = set(ids)

        for stage in self.stages:
            for child in stage.stages or []:
                if child not in declared:
                    raise ValueError(
                        f"composite '{stage.id}' references undeclared stage '{child}'"
                    )
                if child == stage.id:
                    raise ValueError(f"composite '{stage.id}' cannot contain itself")

        for entry in self.flow:
            refs = [entry] if isinstance(entry, str) else (
                entry.parallel if isinstance(entry, ParallelGroup) else []
            )
            for ref in refs:
                if ref not in declared:
                    raise ValueError(f"flow references undeclared stage '{ref}'")
        return self

    def stage(self, stage_id: str) -> StageConfig:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise KeyError(stage_id)

    def validate_prompt_files(self, base_dir: Path) -> None:
        """Prompt paths are relative to the repo root; missing files are
        startup errors (prompts are versioned with the config, never inlined)."""
        missing = [
            s.prompt for s in self.stages
            if s.prompt and not (base_dir / s.prompt).is_file()
        ]
        if missing:
            raise ConfigError(f"missing prompt files: {missing}")


def load_pipeline(path: str | Path, base_dir: Optional[Path] = None) -> PipelineConfig:
    """Load + fully lint a pipeline YAML. Raises ConfigError on any problem."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"pipeline config not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"pipeline config must be a mapping: {path}")
    try:
        config = PipelineConfig.model_validate(raw)
    except Exception as e:  # pydantic ValidationError -> uniform startup error
        raise ConfigError(f"invalid pipeline config {path}: {e}")
    config.validate_prompt_files(base_dir or Path.cwd())
    return config
