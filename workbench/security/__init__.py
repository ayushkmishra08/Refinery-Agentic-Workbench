"""Role-based access control for the workbench.

Nothing in ``data/`` is readable until a principal has been authenticated and the access
policy has cleared them for the classification of the document that holds the answer.

- ``roles``           the role ladder and what clearance each role carries
- ``classification``  which clearance a document needs (the CDU manual is ``confidential``)
- ``auth``            credential store (PBKDF2), lockout, bearer tokens
- ``policy``          the decision: may this principal read this document?
- ``guard``           a KnowledgeService wrapper that drops every record the policy denies

The guard is the enforcement point. Agents receive a guarded service and cannot reach a
document the principal is not cleared for, whatever they ask it for.
"""
from __future__ import annotations

from workbench.security.auth import AuthError, AuthService, Principal, SessionToken
from workbench.security.classification import ClassificationRegistry, DocumentClassification
from workbench.security.guard import GuardedKnowledgeService
from workbench.security.policy import AccessDecision, AccessPolicy
from workbench.security.roles import CLEARANCE_ORDER, Clearance, Role, role_clearance

__all__ = [
    "AuthError", "AuthService", "Principal", "SessionToken",
    "ClassificationRegistry", "DocumentClassification",
    "GuardedKnowledgeService", "AccessDecision", "AccessPolicy",
    "CLEARANCE_ORDER", "Clearance", "Role", "role_clearance",
]
