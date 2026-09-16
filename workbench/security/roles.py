"""The role ladder and the clearance each role carries.

Four clearances, ordered. A principal may read a document whose classification sits at or
below their own clearance; nothing else. The ladder is deliberately short — a refinery
control room has a shift operator, the process engineers, the lead engineer who owns the
unit documentation, and whoever administers the system.
"""
from __future__ import annotations

from enum import Enum


class Clearance(str, Enum):
    PUBLIC = "public"                # anything that could be pinned on a notice board
    INTERNAL = "internal"            # general plant material, no unit-specific operating data
    CONFIDENTIAL = "confidential"    # unit operating manuals: the CDU manual lives here
    SECRET = "secret"                # licensor / contractual material


CLEARANCE_ORDER: dict[Clearance, int] = {
    Clearance.PUBLIC: 0,
    Clearance.INTERNAL: 1,
    Clearance.CONFIDENTIAL: 2,
    Clearance.SECRET: 3,
}


class Role(str, Enum):
    GUEST = "guest"                  # unauthenticated
    OPERATOR = "operator"            # panel / field operator
    ENGINEER = "engineer"            # process engineer
    LEAD_ENGINEER = "lead_engineer"  # owns the unit documentation — cleared for the CDU manual
    ADMIN = "admin"


ROLE_CLEARANCE: dict[Role, Clearance] = {
    Role.GUEST: Clearance.PUBLIC,
    Role.OPERATOR: Clearance.INTERNAL,
    Role.ENGINEER: Clearance.INTERNAL,
    Role.LEAD_ENGINEER: Clearance.CONFIDENTIAL,
    Role.ADMIN: Clearance.SECRET,
}

ROLE_TITLES: dict[Role, str] = {
    Role.GUEST: "Guest",
    Role.OPERATOR: "Operator",
    Role.ENGINEER: "Process Engineer",
    Role.LEAD_ENGINEER: "Lead Engineer",
    Role.ADMIN: "Administrator",
}


def role_clearance(role: Role | str) -> Clearance:
    """The clearance a role carries; an unknown role is treated as a guest."""
    try:
        return ROLE_CLEARANCE[Role(role)]
    except (ValueError, KeyError):
        return Clearance.PUBLIC


def clears(role: Role | str, needed: Clearance | str) -> bool:
    """True when ``role``'s clearance is at or above ``needed``."""
    try:
        required = Clearance(needed)
    except ValueError:
        required = Clearance.CONFIDENTIAL          # an unknown classification is never public
    return CLEARANCE_ORDER[role_clearance(role)] >= CLEARANCE_ORDER[required]


def roles_clearing(needed: Clearance | str) -> list[Role]:
    """Every role whose clearance reaches ``needed``, lowest first — used in denial messages."""
    return [r for r in Role if r is not Role.GUEST and clears(r, needed)]


def title(role: Role | str) -> str:
    try:
        return ROLE_TITLES[Role(role)]
    except (ValueError, KeyError):
        return str(role)
