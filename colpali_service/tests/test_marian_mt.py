"""真實 MarianMT 整合（mt marker）：RUN_MT_TESTS=1 才跑（下載 312MB 模型）。
手動：RUN_MT_TESTS=1 uv run --no-sync pytest colpali_service/tests/test_marian_mt.py -q
（需先 uv sync --package colpali-service --extra gpu --inexact）"""
import os

import pytest

pytest.importorskip("sentencepiece")
pytest.importorskip("opencc")
pytestmark = [
    pytest.mark.mt,
    pytest.mark.skipif(
        os.environ.get("RUN_MT_TESTS") != "1", reason="需 RUN_MT_TESTS=1（下載模型）"
    ),
]


def test_marian_translates_anatomy_query_to_english():
    from colpali_service.translate import build_marian_translator, detect_lang

    tr = build_marian_translator()
    r = tr.translate("肱二頭肌的起止點是什麼？")
    assert r.lang == "zh"
    assert r.translated_q is not None
    assert "biceps brachii" in r.translated_q.lower()   # glossary 保證術語
    assert detect_lang(r.translated_q) == "en"          # 輸出無 CJK 殘留


def test_marian_traditional_chinese_via_opencc():
    from colpali_service.translate import build_marian_translator

    tr = build_marian_translator()
    r = tr.translate("心臟的血液供應")                    # 臟=繁體；t2s 後 MT 才認得
    assert r.translated_q is not None
    assert "blood supply" in r.translated_q.lower()
