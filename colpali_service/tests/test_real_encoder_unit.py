"""RealColPaliEncoder 組合邏輯（無 torch）：runtime 用 shared 的 Mock、translator 用 fake。"""
import importlib.util

import numpy as np
import pytest
from anatomy_shared.binary import binarize, pool_patches
from anatomy_shared.colpali_runtime import MockColPaliRuntime
from colpali_service.real_encoder import RealColPaliEncoder
from colpali_service.translate import LocalTranslator


def _translator(mt_fn=None):
    return LocalTranslator(
        mt_fn=mt_fn or (lambda ts: ["origin and insertion of biceps brachii"] * len(ts)),
        mt_model_name="fake-mt", glossary=[], t2s_fn=lambda s: s,
    )


def test_zh_query_encodes_translated_text():
    rt = MockColPaliRuntime()
    enc = RealColPaliEncoder(runtime=rt, translator=_translator())
    out = enc.encode_query("肱二頭肌的起止點")
    # 以翻譯後英文編碼（DL-020）→ tokens 應等於 mock 對英文的編碼
    ref = rt.encode_query("origin and insertion of biceps brachii")
    assert out["tokens_bin"] == [binarize(t) for t in ref.embeddings[ref.valid_mask]]
    assert out["lang"] == "zh"
    assert out["translated_q"] == "origin and insertion of biceps brachii"
    assert out["mt_model"] == "fake-mt"
    expected_pooled = pool_patches(ref.embeddings, valid_mask=ref.valid_mask).astype("<f4")
    assert np.frombuffer(out["pooled_f32"], dtype="<f4").tolist() == expected_pooled.tolist()


def test_mt_failure_falls_back_to_original_text():
    def boom(ts):
        raise RuntimeError("down")
    rt = MockColPaliRuntime()
    enc = RealColPaliEncoder(runtime=rt, translator=_translator(mt_fn=boom))
    out = enc.encode_query("肱二頭肌")
    ref = rt.encode_query("肱二頭肌")                      # 失敗→原文編碼
    assert out["translated_q"] is None
    assert out["tokens_bin"] == [binarize(t) for t in ref.embeddings[ref.valid_mask]]


def test_en_query_identity():
    enc = RealColPaliEncoder(runtime=MockColPaliRuntime(), translator=_translator())
    out = enc.encode_query("median nerve")
    assert out["lang"] == "en" and out["translated_q"] == "median nerve"


@pytest.mark.skipif(importlib.util.find_spec("torch") is not None,
                    reason="僅在無 torch 環境驗證錯誤訊息（CI unit job）")
def test_get_encoder_real_without_gpu_extra_raises_clear_error(monkeypatch):
    from colpali_service.encoder import get_encoder

    monkeypatch.setenv("ENCODER_MOCK", "false")
    with pytest.raises(RuntimeError, match="gpu"):
        get_encoder()
