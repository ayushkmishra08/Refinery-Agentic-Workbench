"""The role schema: three roles, three document tags, one ladder.

Every document carries a **tag**. Every role sits at a **level**. A role may read a document
when its level reaches the level the document's tag requires — and nothing above it, ever.

    role        level   may read tags                 in this deployment
    ─────────── ─────── ───────────────────────────── ──────────────────────────────────
    guest       0       (nothing)                     not signed in
    user        1       INTERNAL                      API 560, API 610, ejector bulletin, ESBWR
    manager     2       INTERNAL, CONFIDENTIAL        + Crude desalter
    admin       3       INTERNAL, CONFIDENTIAL, SECRET + CDU operating manual

Two properties the rest of the system relies on:

**Its own tree, whole.** A role reads everything at or below its level. A manager is not shown
a redacted Crude desalter — they get all of it, every claim, every procedure, every page.

**Nothing above it, by construction.** The check is a level comparison, not a filter over text,
so there is no wording that widens it. A user who asks for CDU material is refused whatever they
type, because the refusal happens before any CDU record is loaded into the answer.

The one door through the ceiling is an approved access grant (``workbench.security.escalation``),
and it opens for named records only, once, for a few minutes.

``guest`` is a state, not an account: it is who you are before signing in, and it can read
nothing at all.
"""
from __future__ import annotations

from enum import Enum


class Tag(str, Enum):
    """The classification stamped on a document. Order matters: see ``TAG_LEVEL``."""
    INTERNAL = "INTERNAL"            # plant-wide reference: standards, vendor manuals
    CONFIDENTIAL = "CONFIDENTIAL"    # unit equipment documentation
    SECRET = "SECRET"                # unit operating manuals — how the plant is actually run


class Role(str, Enum):
    GUEST = "guest"
    USER = "user"
    MANAGER = "manager"
    ADMIN = "admin"


TAG_LEVEL: dict[Tag, int] = {
    Tag.INTERNAL: 1,
    Tag.CONFIDENTIAL: 2,
    Tag.SECRET: 3,
}

ROLE_LEVEL: dict[Role, int] = {
    Role.GUEST: 0,
    Role.USER: 1,
    Role.MANAGER: 2,
    Role.ADMIN: 3,
}

ROLE_TITLES: dict[Role, str] = {
    Role.GUEST: "Guest",
    Role.USER: "User",
    Role.MANAGER: "Manager",
    Role.ADMIN: "Administrator",
}

ROLE_DESCRIPTIONS: dict[Role, str] = {
    Role.GUEST: "Not signed in. Cannot read any document.",
    Role.USER: "Operations and engineering staff. Reads INTERNAL reference material.",
    Role.MANAGER: "Unit manager. Reads INTERNAL and CONFIDENTIAL equipment documentation.",
    Role.ADMIN: "Documentation owner. Reads everything, and approves manager escalations.",
}

# Who is asked when a role needs something above its level. Admin is the ceiling: an admin
# request has nowhere to go, which is correct — there is nothing an admin cannot already read.
APPROVER_OF: dict[Role, Role | None] = {
    Role.GUEST: None,            # a guest signs in; it does not escalate
    Role.USER: Role.MANAGER,
    Role.MANAGER: Role.ADMIN,
    Role.ADMIN: None,
}

# The tag a document falls back to when nothing identifies it. The highest one: an unrecognised
# document is not a public one.
FALLBACK_TAG = Tag.SECRET


def _role(value: Role | str) -> Role:
    """Coerce to a Role, failing to ``guest``.

    ``str()`` on a ``str``-Enum member returns ``"Role.ADMIN"`` on modern Python, not
    ``"admin"``, so the member case is handled before any string conversion.
    """
    if isinstance(value, Role):
        return value
    try:
        return Role(str(value).strip().lower())
    except ValueError:
        return Role.GUEST            # an unknown role name is a guest, never an admin


def _tag(value: Tag | str) -> Tag:
    if isinstance(value, Tag):
        return value
    try:
        return Tag(str(value).strip().upper())
    except ValueError:
        return FALLBACK_TAG          # an unknown tag is the most restricted one


def level_of(role: Role | str) -> int:
    return ROLE_LEVEL[_role(role)]


def tag_level(tag: Tag | str) -> int:
    return TAG_LEVEL[_tag(tag)]


def can_read(role: Role | str, tag: Tag | str) -> bool:
    """The whole access rule, in one line."""
    return level_of(role) >= tag_level(tag)


def readable_tags(role: Role | str) -> list[Tag]:
    """Every tag this role may read, lowest first."""
    return [t for t in Tag if can_read(role, t)]


def roles_that_can_read(tag: Tag | str) -> list[Role]:
    """Every signed-in role cleared for this tag, lowest first — used in refusal messages."""
    return [r for r in Role if r is not Role.GUEST and can_read(r, tag)]


def normalise_roles(values) -> list[Role]:
    """Coerce a hand-written allowlist to real roles, lowest first, deduplicated.

    Used for the per-document allowlists in ``classification``: the file is meant to be edited by
    hand, so a typo, a duplicate or a stray ``"guest"`` has to land somewhere predictable. An
    unreadable name is dropped rather than widened, and ``guest`` is never an allowed reader —
    it is the state of not having signed in.
    """
    out: list[Role] = []
    for value in values or ():
        if isinstance(value, Role):
            role = value
        else:
            try:
                role = Role(str(value).strip().lower())
            except ValueError:
                continue                 # an unrecognised name grants nothing
        if role is Role.GUEST or role in out:
            continue
        out.append(role)
    return sorted(out, key=lambda r: ROLE_LEVEL[r])


def hardest(tags) -> Tag:
    """The most restricted tag in a collection, by level rather than by name.

    Comparing ``Tag`` members as strings puts ``INTERNAL`` above ``CONFIDENTIAL`` because ``I``
    sorts after ``C``, which would route an escalation to the wrong approver. Level is the only
    ordering that means anything here.
    """
    members = [_tag(t) for t in tags]
    return max(members, key=tag_level) if members else FALLBACK_TAG


def approver_for(role: Role | str) -> Role | None:
    """The role that decides an escalation raised by ``role``."""
    return APPROVER_OF[_role(role)]


def approver_for_tag(tag: Tag | str) -> Role:
    """The lowest role that can approve access to material carrying this tag."""
    candidates = roles_that_can_read(tag)
    return candidates[0] if candidates else Role.ADMIN


def title(role: Role | str) -> str:
    return ROLE_TITLES[_role(role)]


def describe(role: Role | str) -> str:
    return ROLE_DESCRIPTIONS[_role(role)]


def schema() -> dict:
    """The role schema as data — printed by ``workbench security`` and served by the API.

    Having one function produce it means the documentation, the CLI and the API can never drift
    apart from the enforcement, because they all read this.
    """
    return {
        "roles": [
            {"role": r.value, "title": title(r), "level": ROLE_LEVEL[r],
             "description": describe(r),
             "readable_tags": [t.value for t in readable_tags(r)],
             "escalates_to": (approver_for(r).value if approver_for(r) else None)}
            for r in Role
        ],
        "tags": [
            {"tag": t.value, "level": TAG_LEVEL[t],
             "min_role": roles_that_can_read(t)[0].value,
             "roles": [r.value for r in roles_that_can_read(t)]}
            for t in Tag
        ],
        "rule": "a role may read a document when role.level >= tag.level",
        "fallback_tag": FALLBACK_TAG.value,
    }
