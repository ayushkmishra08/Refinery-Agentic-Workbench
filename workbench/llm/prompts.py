"""Loads prompts/agents/<agent>.txt and fills {placeholders}."""
from pathlib import Path


def load_prompt(prompts_dir: Path, name: str, **kwargs: str) -> str:
    text = (prompts_dir / f"{name}.txt").read_text(encoding="utf-8")
    return text.format(**kwargs) if kwargs else text
