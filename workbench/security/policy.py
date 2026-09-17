"""The access decision: which documents may this principal read, and what is withheld?

One place answers that question, so there is one rule to read and one place to audit. It
returns an ``AccessDecision`` rather than a boolean, because the interesting part is what was
withheld: the refusal message, the role that would clear it, and the escalation route all come
from here.

An approved grant (``workbench.security.escalation``) is applied on top: it does not change the
principal's role, it adds named records to the decision for one answer.
"""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from workbench.security.auth import Principal
from workbench.security.classification import ClassificationRegistry, DocumentClassification
from workbench.security.roles import (
    Role, Tag, approver_for, can_read, hardest, normalise_roles, roles_that_can_read, title,
)


class WithheldDocument(BaseModel):
    document_id: str
    title: str = ""
    tag: Tag = Tag.SECRET
    reason: str = ""
    min_role: str = "admin"
    roles: list[str] = Field(default_factory=list)


class AccessDecision(BaseModel):
    """What this principal may read right now, and what is being kept from them."""
    principal: str = "guest"
    role: Role = Role.GUEST
    authenticated: bool = False
    allowed: list[str] = Field(default_factory=list, description="Document ids readable by role alone")
    withheld: list[WithheldDocument] = Field(default_factory=list)
    granted_records: list[str] = Field(default_factory=list, description="Record ids opened by an approved grant, if any")
    granted_documents: list[str] = Field(default_factory=list, description="Documents those records live in")
    grant_id: str | None = None
    ts: float = Field(default_factory=time.time)

    @property
    def denied(self) -> list[str]:
        return [w.document_id for w in self.withheld]

    @property
    def fully_allowed(self) -> bool:
        return not self.withheld

    @property
    def nothing_allowed(self) -> bool:
        return not self.allowed and not self.granted_records

    def required_roles(self) -> list[str]:
        out: list[str] = []
        for w in self.withheld:
            for r in w.roles:
                if r not in out:
                    out.append(r)
        return out

    def escalation_target(self) -> str | None:
        """The role to ask when this principal wants what is being withheld.

        The next rung up, unless the material sits higher than that — a user asking for SECRET
        material needs an administrator, not a manager, because a manager cannot read it either.
        """
        if not self.withheld:
            return None
        hardest_tag = hardest(w.tag for w in self.withheld)
        needed = next(w for w in self.withheld if w.tag == hardest_tag)
        # `roles` is the document's own answer to "who reads this" — the tag ladder where nothing
        # was pinned, an explicit allowlist where it was. Routing off the tag alone would send a
        # compartmented document to a role that cannot open it.
        cleared = normalise_roles(needed.roles) or roles_that_can_read(hardest_tag)
        if not cleared:
            return Role.ADMIN.value
        next_rung = approver_for(self.role)
        if next_rung and next_rung in cleared:
            return next_rung.value
        return cleared[0].value

    def message(self) -> str:
        """The sentence shown to whoever was refused. It names the document and the way forward.

        Naming the document is deliberate. The alternative — pretending it does not exist — would
        make the escalation route unusable, because nobody can request access to something they
        have not been told about. The *contents* never leave; the *existence* does.
        """
        if not self.withheld:
            return ""
        # the document id, not the parsed title: it is the name used by `workbench security`,
        # by the catalogue and by the audit trail, and a refusal that names something else
        # leaves the reader unable to look it up
        names = ", ".join(w.document_id for w in self.withheld)
        tag = hardest(w.tag for w in self.withheld).value
        who = "You are not signed in" if not self.authenticated else f"Your role ({title(self.role)}) is not cleared"
        # No role is named here and no command is quoted. Which role can release the material
        # depends on the *scope* a question turns out to need, which is computed later — naming one
        # now would contradict the request that actually gets raised. And this sentence is read in
        # a browser as often as in a terminal, so it does not tell anyone to type anything.
        route = (" Ask your question anyway: the workstation will say how much of it bears on the"
                 " answer, and raise a request with the role that can release it."
                 if self.authenticated else " Sign in with an account that is cleared.")
        return f"{who} for {names}, which is classified {tag}.{route}"

    def audit_payload(self) -> dict:
        return {"principal": self.principal, "role": self.role.value, "authenticated": self.authenticated,
                "allowed": self.allowed, "withheld": self.denied,
                "grant_id": self.grant_id, "granted_records": len(self.granted_records), "ts": self.ts}


class AccessPolicy:
    """Decides document access for a principal against the classification registry."""

    def __init__(self, registry: ClassificationRegistry, *, enabled: bool = True) -> None:
        self.registry = registry
        self.enabled = enabled

    def may_read(self, principal: Principal, tag: Tag | str | DocumentClassification) -> bool:
        """The whole rule. A classification may name its readers outright; a bare tag cannot."""
        if not self.enabled:
            return True
        if isinstance(tag, DocumentClassification):
            return principal.role in tag.readable_by
        return can_read(principal.role, tag)

    def may_read_document_id(self, principal: Principal, document_id: str) -> bool:
        if not self.enabled:
            return True
        return principal.role in self.registry.readers_of(document_id)

    def decide(self, principal: Principal, documents: list, *, grant=None) -> AccessDecision:
        """Split the loaded documents into readable and withheld, then apply any grant."""
        decision = AccessDecision(principal=principal.username, role=principal.role,
                                  authenticated=principal.authenticated)
        for doc in documents:
            dc = self.registry.classify(doc)
            if self.may_read(principal, dc):
                decision.allowed.append(doc.document_id)
            else:
                decision.withheld.append(WithheldDocument(
                    document_id=doc.document_id, title=doc.title or doc.document_id, tag=dc.tag,
                    reason=dc.reason, min_role=dc.min_role,
                    roles=[r.value for r in dc.readable_by]))
        if grant is not None:
            decision.grant_id = grant.grant_id
            decision.granted_records = list(grant.record_ids)
            decision.granted_documents = list(grant.document_ids)
        return decision
