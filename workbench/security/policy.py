"""The access decision: may this principal read this document?

The policy is the only place that answers that question, so there is one rule to read and
one place to audit. It returns an ``AccessDecision`` rather than a bare boolean: the denial
message a user sees, the roles that would clear the request, and the reason the document is
classified the way it is all come from here.
"""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from workbench.security.auth import Principal
from workbench.security.classification import ClassificationRegistry, DocumentClassification
from workbench.security.roles import Clearance, Role, clears, roles_clearing, title


class AccessDecision(BaseModel):
    allowed: list[str] = Field(default_factory=list, description="Document ids this principal may read")
    denied: list[str] = Field(default_factory=list)
    denied_detail: list[dict] = Field(default_factory=list, description="{document_id, title, clearance, reason, roles}")
    principal: str = "guest"
    role: Role = Role.GUEST
    authenticated: bool = False
    ts: float = Field(default_factory=time.time)

    @property
    def fully_allowed(self) -> bool:
        return not self.denied

    @property
    def nothing_allowed(self) -> bool:
        return not self.allowed

    def required_roles(self) -> list[str]:
        out: list[str] = []
        for d in self.denied_detail:
            for r in d.get("roles", []):
                if r not in out:
                    out.append(r)
        return out

    def message(self) -> str:
        """The sentence shown to the user when something was withheld."""
        if not self.denied:
            return ""
        docs = ", ".join(d.get("title") or d["document_id"] for d in self.denied_detail) or ", ".join(self.denied)
        needed = ", ".join(title(r) for r in self.required_roles()) or "a cleared role"
        who = "You are not signed in" if not self.authenticated else f"Your role ({title(self.role)}) is not cleared"
        return (f"{who} for {docs}. That material is classified "
                f"{self.denied_detail[0]['clearance'] if self.denied_detail else 'confidential'} and is readable by: {needed}.")

    def audit_payload(self) -> dict:
        return {"principal": self.principal, "role": self.role.value, "authenticated": self.authenticated,
                "allowed": self.allowed, "denied": self.denied, "ts": self.ts}


class AccessPolicy:
    """Decides document access for a principal, against a ClassificationRegistry."""

    def __init__(self, registry: ClassificationRegistry, *, enabled: bool = True) -> None:
        self.registry = registry
        self.enabled = enabled

    def may_read(self, principal: Principal, classification: DocumentClassification | Clearance | str) -> bool:
        if not self.enabled:
            return True
        needed = classification.clearance if isinstance(classification, DocumentClassification) else classification
        return clears(principal.role, needed)

    def may_read_document_id(self, principal: Principal, document_id: str) -> bool:
        if not self.enabled:
            return True
        return clears(principal.role, self.registry.clearance_of(document_id))

    def decide(self, principal: Principal, documents: list) -> AccessDecision:
        """Split a list of DocumentInfo into what this principal may and may not read."""
        decision = AccessDecision(principal=principal.username, role=principal.role,
                                  authenticated=principal.authenticated)
        for doc in documents:
            dc = self.registry.classify(doc)
            if self.may_read(principal, dc):
                decision.allowed.append(doc.document_id)
            else:
                decision.denied.append(doc.document_id)
                decision.denied_detail.append({
                    "document_id": doc.document_id, "title": doc.title or doc.document_id,
                    "clearance": dc.clearance.value, "reason": dc.reason,
                    "roles": [r.value for r in roles_clearing(dc.clearance)],
                })
        return decision
