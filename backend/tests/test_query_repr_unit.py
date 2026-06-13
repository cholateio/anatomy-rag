import base64
import struct

import pytest

from anatomy_backend.retrieval.query_repr import QueryRepr


def _payload(n_tokens=3, translated="biceps origin", lang="zh"):
    tokens = [bytes([i]) + b"\x00" * 15 for i in range(n_tokens)]  # 各 16 bytes
    pooled = struct.pack("<128f", *[0.1 * i for i in range(128)])
    return {
        "tokens_bin": [base64.b64encode(t).decode() for t in tokens],
        "pooled_f32": base64.b64encode(pooled).decode(),
        "translated_q": translated, "lang": lang,
        "model": "colpali-v1.3-hf", "mt_model": "opus-mt-zh-en",
    }


def test_from_encode_query_response_decodes_base64():
    qr = QueryRepr.from_encode_query_response(_payload())
    assert len(qr.tokens_bin) == 3
    assert all(len(t) == 16 for t in qr.tokens_bin)
    assert qr.tokens_bin[1][0] == 1
    assert len(qr.pooled_f32) == 128
    assert abs(qr.pooled_f32[10] - 1.0) < 1e-5  # 0.1 * 10
    assert qr.translated_q == "biceps origin"
    assert qr.lang == "zh"


def test_capability_flags():
    qr = QueryRepr.from_encode_query_response(_payload())
    assert qr.has_binary_tokens is True
    assert qr.has_float_multivector is False  # v1 encoder 不回 per-token float


def test_translated_q_null_passthrough():
    qr = QueryRepr.from_encode_query_response(_payload(translated=None))
    assert qr.translated_q is None


def test_rejects_wrong_token_length():
    p = _payload()
    p["tokens_bin"][0] = base64.b64encode(b"\x00" * 15).decode()  # 15 bytes
    with pytest.raises(ValueError, match="16"):
        QueryRepr.from_encode_query_response(p)


def test_rejects_wrong_pooled_length():
    p = _payload()
    p["pooled_f32"] = base64.b64encode(b"\x00" * 256).decode()  # 64 floats
    with pytest.raises(ValueError, match="512"):
        QueryRepr.from_encode_query_response(p)
