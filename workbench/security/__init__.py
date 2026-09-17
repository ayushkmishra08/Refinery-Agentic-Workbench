"""Role-based access control for the workbench.

Nothing in the document set is readable until a principal has signed in and the access policy has
cleared them for the tag the document carries.

    roles           three roles and three document tags, and the one rule that relates them
    classification  which tag each document carries, and why
    auth            credential store (PBKDF2), lockout, bearer tokens
    policy          the decision: which documents may this principal read?
    guard           the enforcement point — a KnowledgeService that drops what the policy denies
    escalation      asking a higher role for named records, and the signed one-time key that opens them
    records         the stable id a single piece of knowledge is known by
    leakcheck       the release gate: nothing leaves unless its provenance is permitted
    audit           the append-only security log

The guard is the enforcement point. Agents receive a guarded service and cannot reach a document
the principal is not cleared for, whatever they ask it for and however the question is worded —
which is why no prompt can talk its way past it.
"""
from __future__ import annotations

from workbench.security.audit import SecurityAudit
from workbench.security.auth import SEED_ACCOUNTS, AuthError, AuthService, Principal, SessionToken
from workbench.security.classification import ClassificationRegistry, DocumentClassification
from workbench.security.escalation import (
    AccessGrant,
    AccessRequest,
    EscalationError,
    EscalationStore,
    ScopeManifest,
)
from workbench.security.guard import GuardedKnowledgeService, SealedKnowledgeView, scope_probe
from workbench.security.leakcheck import LeakReport, check_response, refusal_response
from workbench.security.policy import AccessDecision, AccessPolicy
from workbench.security.records import fingerprint, question_fingerprint, record_id
from workbench.security.roles import Role, Tag, approver_for, can_read, level_of, schema, title

__all__ = [
    "SecurityAudit",
    "SEED_ACCOUNTS", "AuthError", "AuthService", "Principal", "SessionToken",
    "ClassificationRegistry", "DocumentClassification",
    "AccessGrant", "AccessRequest", "EscalationError", "EscalationStore", "ScopeManifest",
    "GuardedKnowledgeService", "SealedKnowledgeView", "scope_probe",
    "LeakReport", "check_response", "refusal_response",
    "AccessDecision", "AccessPolicy",
    "fingerprint", "question_fingerprint", "record_id",
    "Role", "Tag", "approver_for", "can_read", "level_of", "schema", "title",
]
