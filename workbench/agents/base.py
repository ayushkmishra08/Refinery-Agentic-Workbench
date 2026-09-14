"""BaseAgent: one ``run`` method, uniform AgentResult, tool access via the registry, audit hooks."""
from __future__ import annotations

from abc import ABC, abstractmethod

from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult
from workbench.llm.client import LLMClient
from workbench.services.protocols import KnowledgeService


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, knowledge: KnowledgeService, llm: LLMClient | None, prompts_dir):
        self.knowledge, self.llm, self.prompts_dir = knowledge, llm, prompts_dir

    @abstractmethod
    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult: ...

    def _result(self, **kw) -> AgentResult:
        return AgentResult(agent=self.name, **kw)
