"""One-shot security setup: create the role accounts and tag every document.

This is the script that turns a freshly ingested document set into a governed one. It is
idempotent — run it again after dropping a new PDF into ``data/raw`` and it will tag the new
document and leave everything else alone.

What it does:

1. **Accounts.** One per role — ``admin``, ``manager``, ``user`` — created if missing. Existing
   accounts are left alone unless ``--reset-passwords`` is given, so re-running it never
   silently changes a password someone is using.
2. **Tags.** Applies ``ASSIGNMENTS`` below: the CDU operating manual is ``SECRET`` (admin), the
   Crude desalter is ``CONFIDENTIAL`` (manager), everything else is ``INTERNAL`` (user). A
   document that matches no assignment falls to the pattern rules in
   ``workbench.security.classification``, and anything those miss stays ``SECRET``.
3. **Prints the matrix.** Who can read what, so the result can be checked at a glance rather
   than inferred from three config files.

Run it with ``python -m workbench setup-security`` or ``python scripts/setup_security.py``.
"""
from __future__ import annotations

import re
import secrets
import string

from workbench.security.auth import SEED_ACCOUNTS, AuthService
from workbench.security.classification import ClassificationRegistry
from workbench.security.roles import Role, Tag, roles_that_can_read, title

# (pattern matched against the document id and title, tag, reason). First match wins.
# These are *assignments*, not guesses: they are pinned and the rules never overwrite them.
ASSIGNMENTS: list[tuple[str, Tag, str]] = [
    (r"\bcdu\b|\bcrude distillation\b|\bcdu operating manual\b",
     Tag.SECRET, "CDU operating manual — how this unit is actually run"),
    (r"\bdesalter\b|\bdesalting\b",
     Tag.CONFIDENTIAL, "Crude desalter equipment documentation"),
]
DEFAULT_ASSIGNMENT = (Tag.INTERNAL, "published standard or vendor reference material")

PASSWORD_ALPHABET = string.ascii_letters + string.digits + "#@$%&*"


def random_password(length: int = 16) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def assign_tag(document_id: str, title_text: str = "") -> tuple[Tag, str]:
    """The tag this deployment gives a document, and why."""
    haystack = f"{document_id} {title_text}"
    for pattern, tag, reason in ASSIGNMENTS:
        if re.search(pattern, haystack, re.IGNORECASE):
            return tag, reason
    return DEFAULT_ASSIGNMENT


def setup_security(cfg, *, documents=None, reset_passwords: bool = False, random_passwords: bool = False,
                   quiet: bool = False) -> dict:
    """Create the accounts, tag the documents, and return what was done.

    ``documents`` is a list of DocumentInfo; when omitted the loaded knowledge service is asked.
    """
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        if not quiet:
            print(text)

    auth = AuthService(cfg.paths.security_dir)
    registry = ClassificationRegistry(cfg.paths.security_dir / "classifications.json")

    # ---- 1. accounts -------------------------------------------------------------------
    say("Accounts")
    say("--------")
    existing = {u["username"]: u for u in auth.users()}
    credentials: list[dict] = []
    for username, role, default_password, env_var in SEED_ACCOUNTS:
        password = random_password() if random_passwords else default_password
        if username in existing and not reset_passwords:
            # An account still carrying its seeded password is no secret, so say what it is —
            # an operator who cannot sign in cannot check anything. One that has been changed is
            # someone's real password and is never printed.
            seeded = existing[username].get("must_change")
            shown = f"password: {default_password}  (seeded — change it)" if seeded else "password: set by its owner"
            say(f"  {username:<8} {role.value:<8} already exists      {shown}")
            credentials.append({"username": username, "role": role.value,
                                "password": default_password if seeded else None, "status": "existing"})
            continue
        if username in existing:
            auth.set_password(username, password)
            auth.revoke_all(username)
            status = "password reset (all its sessions signed out)"
        else:
            auth.add_user(username, password, role, display_name=title(role), must_change=not random_passwords)
            status = "created"
        say(f"  {username:<8} {role.value:<8} {status}   password: {password}")
        credentials.append({"username": username, "role": role.value, "password": password, "status": status})
    say()

    # ---- 2. document tags --------------------------------------------------------------
    say("Documents")
    say("---------")
    docs = list(documents or [])
    tagged: list[dict] = []
    for doc in sorted(docs, key=lambda d: d.document_id):
        tag, reason = assign_tag(doc.document_id, doc.title or "")
        dc = registry.assign(doc.document_id, tag, reason, by="setup",
                             title=doc.title or doc.document_id, unit=doc.unit)
        who = title(roles_that_can_read(tag)[0])
        say(f"  {doc.document_id[:46]:<46} {tag.value:<13} {who} and above")
        tagged.append({"document_id": doc.document_id, "title": doc.title, "tag": tag.value,
                       "min_role": dc.min_role, "reason": reason})
    if not docs:
        say("  (no documents are loaded; run the knowledge-layer pipeline first)")
    say()

    # ---- 3. the matrix -----------------------------------------------------------------
    say("Who can read what")
    say("-----------------")
    header = f"  {'document':<46} " + " ".join(f"{r.value:>8}" for r in Role if r is not Role.GUEST)
    say(header)
    for row in tagged:
        marks = " ".join(f"{('yes' if any(rr.value == r.value for rr in roles_that_can_read(row['tag'])) else '—'):>8}"
                         for r in Role if r is not Role.GUEST)
        say(f"  {row['document_id'][:46]:<46} {marks}")
    say()
    say("  A guest — anyone who has not signed in — reads nothing at all.")
    say("  A role reads every tag at or below its level, and nothing above it.")
    say("  The only way through the ceiling is an approved access key, which opens named")
    say("  records for one question, once, for a few minutes.")

    return {"accounts": credentials, "documents": tagged, "report": "\n".join(lines)}
