"""The terminal's side of signing in: where the token is kept and how the password is asked for.

The token the orchestrator mints is written to ``data/workbench/security/cli_session.json`` so
one ``workbench login`` covers the rest of the shift rather than every command. The file holds
the token and nothing else about the account; deleting it is a logout.

``ensure_access`` is what ``ask`` and ``repl`` call: it checks the stored token against the
documents that are loaded, and when they are not readable it asks for the password in place,
naming the document and the role required, then retries. That is the prompt the engineer sees
before the CDU material is opened.
"""
from __future__ import annotations

import getpass
import json
import sys
import time
from pathlib import Path

MAX_ATTEMPTS = 3


def _read_password(prompt: str) -> str:
    """Hidden entry at a terminal; a plain read when stdin is a pipe (scripted sign-in, tests).

    ``getpass`` opens the console directly on Windows and raises when there is no console, so
    a piped password would otherwise fail rather than work quietly.
    """
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            return getpass.getpass(prompt)
        except (getpass.GetPassWarning, OSError):
            pass
    print(prompt, end="", flush=True)
    return (sys.stdin.readline() or "").rstrip("\r\n")


def _path(cfg) -> Path:
    return cfg.paths.security_dir / "cli_session.json"


def load_token(cfg) -> str | None:
    """The stored token, or None when there is none or it has expired."""
    p = _path(cfg)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if float(raw.get("expires", 0)) <= time.time():
        clear_token(cfg)
        return None
    return raw.get("token")


def save_token(cfg, principal) -> None:
    p = _path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": principal.token, "username": principal.username,
                             "role": principal.role.value, "expires": principal.expires}, indent=1), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass


def clear_token(cfg) -> None:
    try:
        _path(cfg).unlink()
    except OSError:
        pass


def stored_identity(cfg) -> dict | None:
    p = _path(cfg)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if float(raw.get("expires", 0)) > time.time() else None


def prompt_login(orch, *, username: str | None = None, reason: str = "") -> str | None:
    """Ask for a username and password at the terminal; return the token on success."""
    from workbench.security.auth import AuthError

    if reason:
        print(f"\n  {reason}")
    for attempt in range(MAX_ATTEMPTS):
        try:
            user = username or input("  username [lead]: ").strip() or "lead"
            password = _read_password(f"  password for {user}: ")
        except (EOFError, KeyboardInterrupt):
            print("\n  sign-in cancelled.")
            return None
        try:
            principal = orch.login(user, password, label="cli")
        except AuthError as exc:
            print(f"  {exc}")
            if exc.locked_until:
                return None
            username = None                      # let them correct the username too
            continue
        save_token(orch.cfg, principal)
        note = "  (this account still has its seeded password — change it with 'workbench passwd')" if principal.must_change_password else ""
        print(f"  signed in as {principal.display_name or principal.username} ({principal.role.value}).{note}\n")
        return principal.token
    print("  too many attempts.")
    return None


def ensure_access(orch, *, interactive: bool = True) -> str | None:
    """A token that opens the loaded documents, asking for one at the terminal if needed."""
    if not orch.cfg.security.enabled:
        return None
    token = load_token(orch.cfg)
    principal, decision = orch.access_for(token)
    if not decision.denied:
        return token
    if not interactive or not orch.cfg.security.prompt_on_denial:
        return token
    docs = ", ".join(d.get("title") or d["document_id"] for d in decision.denied_detail)
    needed = ", ".join(decision.required_roles()) or "a cleared role"
    reason = (f"{docs} is classified {decision.denied_detail[0]['clearance']} and needs: {needed}."
              if decision.denied_detail else decision.message())
    new_token = prompt_login(orch, reason=reason)
    return new_token or token
