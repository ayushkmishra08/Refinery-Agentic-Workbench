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
    from workbench.security.roles import Role, describe

    if reason:
        print(f"\n  {reason}")
    if username is None:
        print("  Roles on this system:")
        for role in (Role.USER, Role.MANAGER, Role.ADMIN):
            print(f"    {role.value:<8} {describe(role)}")
    for attempt in range(MAX_ATTEMPTS):
        try:
            user = username or input("  username [user]: ").strip() or "user"
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


def require_sign_in(orch, *, interactive: bool = True) -> str | None:
    """Establish who is asking, before any question is accepted.

    The workbench asks for a role first and answers second. That ordering is the point: a
    question is never evaluated on behalf of an unknown caller, so there is no window in which
    retrieval has run but identity has not been established.

    Returns a token, or None when the caller declined and the run should proceed as a guest
    (which reads nothing, and will be told so).
    """
    if not orch.cfg.security.enabled:
        return None
    token = load_token(orch.cfg)
    principal = orch.principal(token)
    if principal.authenticated:
        return token
    if not interactive:
        return None
    print()
    print("  This workbench holds classified unit documentation. Sign in before asking a question.")
    for doc in orch.document_catalogue():
        print(f"    {doc['document_id'][:46]:<46} {doc['tag']:<13} {doc['min_role']} and above")
    print()
    return prompt_login(orch, reason="")


def ensure_access(orch, *, interactive: bool = True) -> str | None:
    """A token that opens as much as possible, offering a sign-in when something is withheld.

    Used after ``require_sign_in``: a signed-in user who is simply not cleared for one document
    is not re-prompted, because re-typing their own password will not change their role. They
    are only offered the prompt when nobody is signed in at all.
    """
    if not orch.cfg.security.enabled:
        return None
    token = load_token(orch.cfg)
    principal = orch.principal(token)
    if principal.authenticated:
        return token
    if not interactive or not orch.cfg.security.prompt_on_denial:
        return token
    return prompt_login(orch, reason="No one is signed in, so no document is readable.") or token
