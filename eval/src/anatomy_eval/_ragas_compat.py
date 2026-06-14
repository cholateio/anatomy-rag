"""Compatibility shim for ragas==0.4.3 + langchain-community>=0.3.

ragas 0.4.3 performs a top-level import of
``langchain_community.chat_models.vertexai.ChatVertexAI`` and
``langchain_community.llms.VertexAI`` (the latter succeeds via lazy loading;
the former fails because langchain-community 0.3+ removed the vertexai module).

This shim injects a stub module into ``sys.modules`` **before** ragas is
imported so the import succeeds.  The stub class is never instantiated or
invoked in our offline / fake-LLM paths.

Call ``_ensure_compat()`` at the top of every module that imports ragas.
"""
import sys
import types


def _ensure_compat() -> None:
    """Inject the langchain-community vertexai stub if it is missing."""
    key = "langchain_community.chat_models.vertexai"
    if key not in sys.modules:
        m = types.ModuleType(key)

        class ChatVertexAI:  # noqa: N801
            """Stub: never instantiated; satisfies ragas 0.4.3 top-level import."""

        m.ChatVertexAI = ChatVertexAI  # type: ignore[attr-defined]
        sys.modules[key] = m
