"""pytest conftest for eval/tests.

Applies the langchain-community vertexai compatibility shim at module level so
that ragas 0.4.3 can be imported when test files are collected.  This must run
before any test module imports ragas.
"""
from anatomy_eval._ragas_compat import _ensure_compat

_ensure_compat()
