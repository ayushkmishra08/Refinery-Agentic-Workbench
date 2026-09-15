"""Prompt loader: workbench/prompts/<name>.txt with {placeholders}; missing files raise clearly."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=64)
def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def load_prompt(prompts_dir: Path, name: str, **kwargs: str) -> str:
    path = prompts_dir / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt {name} not found in {prompts_dir}")
    text = _read(str(path))
    if kwargs:
        for k, v in kwargs.items():
            text = text.replace("{" + k + "}", str(v))
    return text
