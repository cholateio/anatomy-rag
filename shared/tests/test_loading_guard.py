"""check_loading_info 守門（torch-free 單元）——精確 allowlist + error_msgs。"""
import pytest
from anatomy_shared.colpali_runtime import check_loading_info


def test_clean_info_passes():
    check_loading_info({"missing_keys": [], "unexpected_keys": [],
                        "mismatched_keys": [], "error_msgs": []})


def test_tied_lm_head_exact_keys_are_expected():
    check_loading_info({"missing_keys": ["vlm.lm_head.weight"]})
    check_loading_info({"missing_keys": ["model.lm_head.weight"]})


def test_other_missing_key_raises():
    with pytest.raises(RuntimeError, match="uninitialized"):
        check_loading_info({"missing_keys": ["vlm.model.layers.0.self_attn.q_proj.weight"]})


def test_substring_lookalike_is_not_allowlisted():
    with pytest.raises(RuntimeError):                       # 子字串相似不可放行（精確比對）
        check_loading_info({"missing_keys": ["vlm.lm_head.weight.lora_A"]})


def test_unexpected_mismatched_error_msgs_raise():
    with pytest.raises(RuntimeError):
        check_loading_info({"unexpected_keys": ["foo"]})
    with pytest.raises(RuntimeError):
        check_loading_info({"mismatched_keys": [("w", (1,), (2,))]})
    with pytest.raises(RuntimeError):
        check_loading_info({"error_msgs": ["size mismatch for vlm..."]})
