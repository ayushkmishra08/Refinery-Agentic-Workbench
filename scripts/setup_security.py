#!/usr/bin/env python
"""Create the role accounts and tag every loaded document.

    python scripts/setup_security.py                     # create what is missing, keep passwords
    python scripts/setup_security.py --reset-passwords   # also reset the three role passwords
    python scripts/setup_security.py --random-passwords  # ... to strong random ones, printed once

Equivalent to ``python -m workbench setup-security``. Safe to re-run: existing accounts keep
their passwords unless you ask otherwise, and re-tagging a document is idempotent.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbench.config import load_config                       # noqa: E402
from workbench.security.setup import setup_security            # noqa: E402
from workbench.services.knowledge import build_knowledge_service  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up roles, accounts and document tags.")
    parser.add_argument("--reset-passwords", action="store_true",
                        help="reset the three role passwords (signs out every session for them)")
    parser.add_argument("--random-passwords", action="store_true",
                        help="use strong random passwords instead of the documented defaults; implies --reset-passwords")
    args = parser.parse_args()

    cfg = load_config()
    documents = build_knowledge_service(cfg).documents()
    print("Refinery workbench — security setup\n")
    setup_security(cfg, documents=documents,
                   reset_passwords=args.reset_passwords or args.random_passwords,
                   random_passwords=args.random_passwords)
    print("\nDone. Sign in with `python -m workbench login`.")


if __name__ == "__main__":
    main()
