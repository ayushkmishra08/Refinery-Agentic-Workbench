"""CLI: ``python -m workbench ask "..."`` and ``python -m workbench repl``."""
from __future__ import annotations

import argparse


def main() -> None:
    p = argparse.ArgumentParser(prog="workbench")
    sub = p.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask"); ask.add_argument("text"); ask.add_argument("--session", default="default")
    sub.add_parser("repl")
    args = p.parse_args()
    raise SystemExit(f"not built yet ({args.cmd}); see docs/PLAN.md")
