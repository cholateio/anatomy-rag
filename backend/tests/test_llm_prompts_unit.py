import pytest
from anatomy_backend.llm import prompts


def test_system_prompt_is_versioned_constant():
    assert prompts.ACTIVE_SYSTEM_PROMPT_VERSION in prompts.SYSTEM_PROMPTS
    active = prompts.get_system_prompt()
    assert active is prompts.SYSTEM_PROMPTS[prompts.ACTIVE_SYSTEM_PROMPT_VERSION]
    assert active == prompts.SYSTEM_PROMPT_V1


def test_system_prompt_enforces_citation_and_no_fabrication():
    p = prompts.get_system_prompt()
    assert "[書名簡寫, 頁碼, 圖號" in p
    assert "教材中查無此項" in p


def test_system_prompt_has_no_refusal_rule():
    p = prompts.get_system_prompt()
    for banned in ("拒答", "請諮詢醫師", "請就醫", "無法回答臨床"):
        assert banned not in p


def test_get_system_prompt_unknown_version_raises():
    with pytest.raises(KeyError):
        prompts.get_system_prompt("v999")


def test_build_user_text_single_turn():
    out = prompts.build_user_text("肱二頭肌起於喙突…", "肱二頭肌起點？")
    assert "【教科書摘錄】" in out
    assert "肱二頭肌起於喙突…" in out
    assert "【使用者問題】" in out
    assert "肱二頭肌起點？" in out
    assert "前一問" not in out


def test_build_user_text_followup_carries_only_prev_question():
    out = prompts.build_user_text(
        text_context="尺神經支配…",
        user_query="那它的神經支配呢？",
        prev_query="肱二頭肌起點？",
    )
    assert "前一問：肱二頭肌起點？" in out
    assert "當前追問：那它的神經支配呢？" in out
    assert "尺神經支配…" in out
