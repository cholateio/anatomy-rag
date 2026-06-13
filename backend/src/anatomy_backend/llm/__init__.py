"""LLM 生成層（Phase 6）。

build_llm(settings)：settings.llm_mock=True（預設）→ MockLLMClient（零 OpenAI 呼叫）；
否則 → ModelFallbackClient（gpt-5.5 主 / gpt-5.4 備）。Phase 8 orchestrator 由此取客戶端。
settings 為 duck-typed（只讀 llm_mock / openai_* 屬性）。
"""
from anatomy_backend.llm.client import (
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_COMPLETION_TOKENS,
    DEFAULT_TEMPERATURE,
    LLMClient,
    LLMClientProtocol,
    PIILeakError,
    assert_no_identifiers,
    build_chat_messages,
)
from anatomy_backend.llm.fallback import ModelFallbackClient
from anatomy_backend.llm.image_routing import (
    DEFAULT_IMAGE_COUNT,
    DL009_MAX_IMAGES,
    ImageRoutingDecision,
    QueryIntent,
    route_images,
)
from anatomy_backend.llm.mock import MockLLMClient
from anatomy_backend.llm.prompts import (
    ACTIVE_SYSTEM_PROMPT_VERSION,
    SYSTEM_PROMPTS,
    build_user_text,
    get_system_prompt,
)


def build_llm(settings) -> LLMClientProtocol:
    if getattr(settings, "llm_mock", True):
        return MockLLMClient()
    primary = LLMClient(
        settings.openai_model_primary,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    fallback = LLMClient(
        settings.openai_model_fallback,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    return ModelFallbackClient(primary, fallback)


__all__ = [
    "ACTIVE_SYSTEM_PROMPT_VERSION",
    "DEFAULT_IMAGE_COUNT",
    "DEFAULT_IMAGE_DETAIL",
    "DEFAULT_MAX_COMPLETION_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DL009_MAX_IMAGES",
    "ImageRoutingDecision",
    "LLMClient",
    "LLMClientProtocol",
    "MockLLMClient",
    "ModelFallbackClient",
    "PIILeakError",
    "QueryIntent",
    "SYSTEM_PROMPTS",
    "assert_no_identifiers",
    "build_chat_messages",
    "build_llm",
    "build_user_text",
    "get_system_prompt",
    "route_images",
]
