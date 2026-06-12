"""translate.py 純邏輯測試：CJK 偵測 / glossary / 分段 / 失敗 fallback（DL-020）。

fake mt_fn / t2s_fn 注入，無 torch、無模型下載；真實 Marian 在 test_marian_mt.py（mt marker）。
"""
from colpali_service.translate import (
    LocalTranslator,
    apply_glossary,
    detect_lang,
    load_glossary,
    split_cjk_runs,
)


def test_detect_lang():
    assert detect_lang("肱二頭肌的起止點") == "zh"
    assert detect_lang("origin of biceps brachii") == "en"
    assert detect_lang("biceps 的起止點") == "zh"   # 混語含 CJK 即 zh


def test_split_cjk_runs_preserves_order_and_content():
    parts = split_cjk_runs("biceps brachii的起止點?")
    assert parts == ["biceps brachii", "的起止點", "?"]


def test_load_glossary_longest_first(tmp_path):
    p = tmp_path / "g.tsv"
    p.write_text("# comment\n股骨\tfemur\n股骨頭\tfemoral head\n", encoding="utf-8")
    g = load_glossary(p)
    assert g[0] == ("股骨頭", "femoral head")     # 長詞優先
    assert ("股骨", "femur") in g


def test_apply_glossary_longest_match():
    g = [("股骨頭", "femoral head"), ("股骨", "femur")]
    assert apply_glossary("股骨頭的血液供應", g) == "femoral head的血液供應"


def _fake_translator(mt_fn=None, glossary=()):
    return LocalTranslator(
        mt_fn=mt_fn or (lambda texts: ["MT-SEG"] * len(texts)),   # 英文輸出（CJK 殘留檢查須過）
        mt_model_name="fake-mt",
        glossary=list(glossary),
        t2s_fn=lambda s: s.replace("臟", "脏"),   # 假繁→簡：可觀察轉換有發生
    )


def test_translate_en_identity():
    r = _fake_translator().translate("origin of biceps brachii")
    assert r.lang == "en" and r.translated_q == "origin of biceps brachii"


def test_translate_zh_pipeline_t2s_then_glossary_then_segment():
    # glossary key 以簡體比對（載入端已轉簡）；非 CJK 段（ASCII 術語）不送 MT；
    # 有語意的虛詞（與）照送 MT（不可丟 或/在/是/與 等）
    tr = _fake_translator(glossary=[("心脏", "heart")])
    r = tr.translate("biceps brachii 與心臟的位置")
    assert r.lang == "zh"
    assert "biceps brachii" in r.translated_q          # ASCII span 原樣保留
    assert "heart" in r.translated_q                   # glossary 在 t2s 之後命中
    assert "MT-SEG" in r.translated_q                  # 殘餘 CJK 段（與、的位置）送了 MT
    assert "心" not in r.translated_q                  # CJK 不殘留


def test_punctuation_only_output_is_failure():
    """輸出只剩標點 → 不得宣稱翻譯成功：(a) 全段被丟棄；(b) MT 真的吐回標點。"""
    tr = _fake_translator(mt_fn=lambda texts: ["?"] * len(texts))
    assert tr.translate("的？").translated_q is None      # (a) 虛詞丟棄後只剩標點，MT 未被呼叫
    # (b) CJK 段送 MT、MT 回 "?" → ASCII 守門擋下
    assert tr.translate("與肱二頭肌").translated_q is None


def test_translate_mt_failure_returns_null_not_raise():
    def boom(texts):
        raise RuntimeError("mt down")
    r = _fake_translator(mt_fn=boom).translate("肱二頭肌")
    assert r.lang == "zh" and r.translated_q is None   # §5.1：失敗不阻斷，translated_q=null


def test_translate_output_count_mismatch_is_failure():
    """MT 回傳段數不符 → 視為失敗（不可 zip 靜默截斷）。"""
    r = _fake_translator(mt_fn=lambda texts: ["only-one"]).translate("肱二頭肌，與，橈神經")
    assert r.translated_q is None


def test_translate_residual_cjk_is_failure():
    """MT 輸出仍含 CJK（如 Marian 原樣吐回中文）→ 視為失敗，不得以 zh 文宣稱英譯成功。"""
    r = _fake_translator(mt_fn=lambda texts: list(texts)).translate("肱二頭肌")
    assert r.translated_q is None


def test_particle_only_runs_are_dropped_not_translated():
    """glossary 把兩側術語都換掉後，殘留的單一虛詞段（的）直接丟棄，不送 MT。"""
    calls: list[list[str]] = []

    def spy(texts):
        calls.append(texts)
        return ["x"] * len(texts)

    tr = LocalTranslator(
        mt_fn=spy, mt_model_name="fake-mt",
        glossary=[("肱二頭肌", "biceps brachii"), ("神經支配", "innervation")],
        t2s_fn=lambda s: s,
    )
    r = tr.translate("肱二頭肌的神經支配")
    assert r.translated_q == "biceps brachii innervation"
    assert calls == []                            # 全部段都是術語或虛詞 → 完全不需 MT


def test_default_glossary_file_loads_and_hits():
    tr = _fake_translator(glossary=load_glossary())     # 預設套件內 TSV（key 原為繁體）
    # 預設 t2s 是 fake（不轉這些字）→ 直接用繁體 key 命中即可驗證檔案格式正確
    out = apply_glossary("肱二頭肌的起止點", tr.glossary)
    assert "biceps brachii" in out and "origin and insertion" in out
