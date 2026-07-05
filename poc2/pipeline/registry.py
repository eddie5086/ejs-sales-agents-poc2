"""Stage-strategy and barrier-condition registries.

Each stage resolves `(kind, strategy)` to a plain function — poc1's
one-module-per-agent pattern survives as strategy implementations. Strategies
take a StageContext and return a JSON-able output (or a pydantic model).

Barrier conditions are named predicates over the accumulated run outputs;
`flow: [{barrier: {require: [account_valid]}}]` resolves each name here.
"""
from __future__ import annotations

from typing import Any, Callable

StrategyFn = Callable[["StageContext"], Any]  # noqa: F821 (defined in engine)
ConditionFn = Callable[[dict], bool]

_STRATEGIES: dict[tuple[str, str], StrategyFn] = {}
_CONDITIONS: dict[str, ConditionFn] = {}


class RegistryError(KeyError):
    pass


def register(kind: str, strategy: str) -> Callable[[StrategyFn], StrategyFn]:
    def deco(fn: StrategyFn) -> StrategyFn:
        key = (kind, strategy)
        if key in _STRATEGIES:
            raise RegistryError(f"strategy already registered: {key}")
        _STRATEGIES[key] = fn
        return fn
    return deco


def resolve(kind: str, strategy: str) -> StrategyFn:
    try:
        return _STRATEGIES[(kind, strategy)]
    except KeyError:
        raise RegistryError(
            f"no strategy registered for kind='{kind}' strategy='{strategy}'"
        )


def register_condition(name: str) -> Callable[[ConditionFn], ConditionFn]:
    def deco(fn: ConditionFn) -> ConditionFn:
        if name in _CONDITIONS:
            raise RegistryError(f"condition already registered: {name}")
        _CONDITIONS[name] = fn
        return fn
    return deco


def resolve_condition(name: str) -> ConditionFn:
    try:
        return _CONDITIONS[name]
    except KeyError:
        raise RegistryError(f"no barrier condition registered: '{name}'")
