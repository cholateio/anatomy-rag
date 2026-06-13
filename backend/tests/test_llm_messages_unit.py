import base64
import inspect

import pytest
from anatomy_backend.llm import (
    PIILeakError,
    assert_no_identifiers,
    build_chat_messages,
)


def test_text_only_message_has_no_image_parts():
    msgs = build_chat_messages("SYS", "USER 問題", images=[])
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == [{"type": "text", "text": "USER 問題"}]


def test_image_part_shape_and_detail():
    msgs = build_chat_messages("SYS", "U", images=[b"\x89PNG_fake"], image_detail="high")
    img = msgs[1]["content"][1]
    assert img["type"] == "image_url"
    assert img["image_url"]["detail"] == "high"
    assert img["image_url"]["url"].startswith("data:image/png;base64,")
    b64 = img["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(b64) == b"\x89PNG_fake"


def test_multiple_images_preserve_order_and_detail():
    msgs = build_chat_messages("S", "U", images=[b"a", b"b"], image_detail="low")
    parts = msgs[1]["content"]
    assert len(parts) == 3
    assert all(p["image_url"]["detail"] == "low" for p in parts[1:])


def test_build_chat_messages_has_no_user_id_param():
    # 結構性防線（非唯一防線）：簽章無 user_id
    assert "user_id" not in inspect.signature(build_chat_messages).parameters


def test_assert_no_identifiers_passes_for_clean_payload():
    msgs = build_chat_messages("系統提示", "肱二頭肌起點？", images=[b"img"])
    # 無 forbidden → 直接通過
    assert_no_identifiers(msgs, frozenset())
    # 有 forbidden 但未出現 → 通過
    assert_no_identifiers(msgs, frozenset({"00000000-0000-0000-0000-000000000001"}))


@pytest.mark.parametrize("field", ["system", "user"])
def test_assert_no_identifiers_fail_closed_when_id_embedded(field):
    # Codex F3：識別碼若被誤嵌入 system 或 user 字串，fail-closed 攔下
    uid = "stud-2026-00042"
    system = f"系統提示 {uid}" if field == "system" else "系統提示"
    user = f"肱二頭肌起點？ {uid}" if field == "user" else "肱二頭肌起點？"
    msgs = build_chat_messages(system, user, images=[b"img"])
    with pytest.raises(PIILeakError):
        assert_no_identifiers(msgs, frozenset({uid}))


def test_pii_error_message_does_not_echo_identifier():
    uid = "stud-secret-999"
    msgs = build_chat_messages("S", f"q {uid}", images=[])
    with pytest.raises(PIILeakError) as ei:
        assert_no_identifiers(msgs, frozenset({uid}))
    assert uid not in str(ei.value)  # 不二次外洩
