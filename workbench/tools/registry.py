from __future__ import annotations

from collections.abc import Callable

_TOOLS: dict[str, Callable] = {}


def tool(name: str):
    def deco(fn: Callable):
        _TOOLS[name] = fn
        return fn
    return deco


def get_tool(name: str) -> Callable:
    return _TOOLS[name]


def list_tools() -> list[str]:
    return sorted(_TOOLS)
