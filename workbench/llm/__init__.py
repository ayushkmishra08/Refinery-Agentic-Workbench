"""Local LLM access (Ollama). Agents call ``llm.structured()`` with a pydantic schema and catch LLMUnavailable."""
from workbench.llm.client import BaseLLM, LLMOutputError, LLMUnavailable, NullLLM, OllamaClient, build_llm  # noqa: F401
