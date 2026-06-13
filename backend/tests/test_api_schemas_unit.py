"""Unit tests for api/schemas.py — useChat normalisation, follow-up detection, PageCitation.

Includes F6/M override validation tests (query length, UUID, metadata_filter type).
"""
import pytest
from anatomy_backend.api.schemas import PageCitation, normalize_chat


def _msg(role, text):
    return {"role": role, "parts": [{"type": "text", "text": text}]}


def test_single_turn_no_prev():
    body = {"messages": [_msg("user", "肱二頭肌起點？")]}
    n = normalize_chat(body)
    assert n.query == "肱二頭肌起點？"
    assert n.prev_query is None
    assert n.is_followup is False


def test_reads_only_last_two_user_messages():
    body = {"messages": [
        _msg("user", "第一問很久以前"),
        _msg("assistant", "答一"),
        _msg("user", "肱二頭肌起點？"),
        _msg("assistant", "答二"),
        _msg("user", "那它的神經支配呢？"),
    ]}
    n = normalize_chat(body)
    assert n.query == "那它的神經支配呢？"
    assert n.prev_query == "肱二頭肌起點？"   # 倒數第二則 user，非 assistant
    assert "第一問" not in (n.prev_query or "")  # 更早歷史不得進入


def test_followup_detected_by_pronoun():
    body = {"messages": [_msg("user", "肱二頭肌起點？"), _msg("assistant", "x"),
                         _msg("user", "那它的神經支配呢？")]}
    assert normalize_chat(body).is_followup is True


def test_followup_detected_by_short_length():
    body = {"messages": [_msg("user", "胸鎖乳突肌的作用是什麼？"), _msg("assistant", "x"),
                         _msg("user", "起點")]}  # <8 字
    assert normalize_chat(body).is_followup is True


def test_not_followup_without_prev_even_if_pronoun():
    body = {"messages": [_msg("user", "它")]}  # 無前一問 → 不算追問
    n = normalize_chat(body)
    assert n.is_followup is False
    assert n.prev_query is None


def test_metadata_filter_and_conversation_id_passthrough():
    body = {
        "messages": [_msg("user", "q")],
        "metadata_filter": {"anatomy_system": "musculoskeletal"},
        "conversation_id": "11111111-1111-1111-1111-111111111111",
    }
    n = normalize_chat(body)
    assert n.metadata_filter == {"anatomy_system": "musculoskeletal"}
    assert n.conversation_id == "11111111-1111-1111-1111-111111111111"


def test_content_string_messages_also_supported():
    # 部分 useChat 版本送 content 字串而非 parts
    body = {"messages": [{"role": "user", "content": "純文字訊息"}]}
    assert normalize_chat(body).query == "純文字訊息"


def test_empty_or_no_user_message_raises():
    with pytest.raises(ValueError):
        normalize_chat({"messages": [_msg("assistant", "x")]})
    with pytest.raises(ValueError):
        normalize_chat({"messages": []})


def test_page_citation_shape():
    c = PageCitation(book_title="Gray", edition="42", page=812, figure="Fig.7-23",
                     image_url="https://s3/x.png", snippet="…", score=0.9)
    d = c.model_dump()
    assert d["page"] == 812 and d["figure"] == "Fig.7-23"


# ── F6/M override: input validation (§5.7) ──────────────────────────────────

def test_query_too_long_raises_value_error():
    """query > 2000 chars MUST raise ValueError (F6/M)."""
    long_query = "x" * 2001
    body = {"messages": [_msg("user", long_query)]}
    with pytest.raises(ValueError, match="2000"):
        normalize_chat(body)


def test_query_at_max_length_is_ok():
    """query == 2000 chars is valid (boundary)."""
    body = {"messages": [_msg("user", "x" * 2000)]}
    n = normalize_chat(body)
    assert len(n.query) == 2000


def test_query_empty_raises_value_error():
    """len(query) < 1 (empty after stripping) MUST raise ValueError."""
    # An empty text string — the message exists but has no content
    body = {"messages": [{"role": "user", "content": ""}]}
    with pytest.raises(ValueError):
        normalize_chat(body)


def test_bad_uuid_conversation_id_raises_value_error():
    """Invalid UUID in conversation_id MUST raise ValueError (F6/M)."""
    body = {"messages": [_msg("user", "q")], "conversation_id": "not-a-uuid"}
    with pytest.raises(ValueError):
        normalize_chat(body)


def test_valid_uuid_conversation_id_passes():
    """Valid UUID passes validation."""
    body = {"messages": [_msg("user", "q")],
            "conversation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    n = normalize_chat(body)
    assert n.conversation_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_non_dict_metadata_filter_raises_value_error():
    """metadata_filter that is not dict|None MUST raise ValueError (F6/M)."""
    body = {"messages": [_msg("user", "q")], "metadata_filter": ["list", "is", "invalid"]}
    with pytest.raises(ValueError):
        normalize_chat(body)


def test_none_metadata_filter_is_ok():
    """metadata_filter=None is explicitly allowed."""
    body = {"messages": [_msg("user", "q")], "metadata_filter": None}
    n = normalize_chat(body)
    assert n.metadata_filter is None
