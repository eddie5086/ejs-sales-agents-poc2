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
# Fan-out items provider: (payload, outputs) -> list of dict-like items.
ItemsFn = Callable[[dict, dict], list]

_STRATEGIES: dict[tuple[str, str], StrategyFn] = {}
_CONDITIONS: dict[str, ConditionFn] = {}
_ITEMS: dict[str, ItemsFn] = {}


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


def register_items(name: str) -> Callable[[ItemsFn], ItemsFn]:
    """Register a fan-out items provider, referenced from stage params as
    `items_from: <name>` (e.g. verify fans over the whole contact pool while
    generate fans over the reconciled selection)."""
    def deco(fn: ItemsFn) -> ItemsFn:
        if name in _ITEMS:
            raise RegistryError(f"items provider already registered: {name}")
        _ITEMS[name] = fn
        return fn
    return deco


def resolve_items(name: str) -> ItemsFn:
    try:
        return _ITEMS[name]
    except KeyError:
        raise RegistryError(f"no fan-out items provider registered: '{name}'")
