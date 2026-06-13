from types import SimpleNamespace

from anatomy_backend.llm import build_llm
from anatomy_backend.llm.fallback import ModelFallbackClient
from anatomy_backend.llm.mock import MockLLMClient


def _settings(**over):
    base = dict(
        llm_mock=True,
        openai_api_key="sk-dummy",
        openai_model_primary="gpt-5.5",
        openai_model_fallback="gpt-5.4",
        openai_base_url="https://api.openai.com/v1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_build_llm_returns_mock_when_llm_mock_true():
    assert isinstance(build_llm(_settings(llm_mock=True)), MockLLMClient)


def test_build_llm_returns_fallback_when_llm_mock_false():
    # 僅建構（AsyncOpenAI 離線建立），不打 API
    assert isinstance(build_llm(_settings(llm_mock=False)), ModelFallbackClient)
