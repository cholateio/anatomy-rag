"""DL-020 本地查詢翻譯（zh/混語 → en）。

管線：CJK 偵測 → OpenCC t2s（繁→簡）→ glossary 長詞優先替換（出英文術語）→
CJK-run 分段、僅 CJK 段送 MarianMT（greedy）→ 空白 join。
非 CJK 段（ASCII/拉丁術語、數字、標點）原樣保留——「span 保護」不用 placeholder
（sentencepiece 會拆壞 placeholder），用分段繞過。
任一步例外或輸出守門不過 → translated_q=None（§5.1：MT 失敗不阻斷查詢）。
本模組 import 必須 torch-free；重依賴只在 build_marian_translator() 內 lazy import。
"""
import logging
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

# CJK Ext-A（U+3400–4DBF）+ 基本區（U+4E00–9FFF）；不含 Ext-B+
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_CJK_RUN_RE = re.compile(r"([㐀-䶿一-鿿]+)")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
# glossary 替換後殘留的「所有格/連接」虛詞段：直接丟棄不送 MT。
# 僅列語意可安全省略者（的/之）；與/和/或/在/是等有語意，照送 MT。
_PARTICLE_RUNS = frozenset({"的", "之"})

DEFAULT_MT_MODEL = "Helsinki-NLP/opus-mt-zh-en"   # DECIDED（DL-020）


def detect_lang(text: str) -> str:
    """含 CJK 字元即視為需翻譯的中文/混語 query（DL-020）。"""
    return "zh" if _CJK_RE.search(text) else "en"


def split_cjk_runs(text: str) -> list[str]:
    """切成 CJK-run / 非 CJK-run 交錯片段（保序、strip、去空段）。"""
    return [p.strip() for p in _CJK_RUN_RE.split(text) if p.strip()]


def load_glossary(path: str | Path | None = None) -> list[tuple[str, str]]:
    """讀 TSV（term\\ttranslation；# 開頭為註解），回傳依 key 長度遞減排序的清單。"""
    if path is None:
        src = resources.files("colpali_service").joinpath("glossary_zh_en.tsv")
        text = src.read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    entries: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"glossary 第 {lineno} 行格式錯誤（須為 term\\ttranslation）：{line!r}"
            )
        entries.append((parts[0], parts[1]))
    return sorted(entries, key=lambda kv: -len(kv[0]))


def apply_glossary(text: str, glossary: list[tuple[str, str]]) -> str:
    """長詞優先逐一替換（glossary 已排序）。替換結果為英文，不會再被後續中文 key 誤中。"""
    for term, translation in glossary:
        text = text.replace(term, translation)
    return text


@dataclass(frozen=True)
class TranslationResult:
    translated_q: str | None   # en=原文；zh 成功=英文；MT 失敗=None（§5.1）
    lang: str                  # "zh" | "en"
    mt_model: str


class LocalTranslator:
    """組合式翻譯器：mt_fn / t2s_fn 以 callable 注入（單元測試免 torch/模型）。"""

    def __init__(self, mt_fn, mt_model_name: str, glossary: list[tuple[str, str]], t2s_fn):
        self._mt = mt_fn                      # Callable[[list[str]], list[str]]
        self.mt_model_name = mt_model_name
        self._t2s = t2s_fn                    # Callable[[str], str]
        # glossary key 轉簡，與 t2s 後的 query 在同一文字空間比對（長度可能變，重排序）
        self.glossary = sorted(
            [(t2s_fn(term), tr) for term, tr in glossary], key=lambda kv: -len(kv[0])
        )

    def translate(self, q: str) -> TranslationResult:
        lang = detect_lang(q)
        if lang == "en":
            return TranslationResult(translated_q=q, lang="en", mt_model=self.mt_model_name)
        try:
            text = apply_glossary(self._t2s(q), self.glossary)
            # 丟棄純虛詞段（glossary 替換後殘渣），其餘 CJK 段送 MT
            parts = [p for p in split_cjk_runs(text) if p not in _PARTICLE_RUNS]
            cjk_idx = [i for i, p in enumerate(parts) if _CJK_RE.search(p)]
            if cjk_idx:
                translated = self._mt([parts[i] for i in cjk_idx])
                if len(translated) != len(cjk_idx):          # zip 靜默截斷＝隱性失敗
                    raise RuntimeError(
                        f"MT 輸出段數 {len(translated)} != 輸入段數 {len(cjk_idx)}")
                for i, t in zip(cjk_idx, translated, strict=True):
                    parts[i] = t.strip()
            result = " ".join(p for p in parts if p)
            if not result or _CJK_RE.search(result):         # CJK 殘留＝未真正翻成英文
                raise RuntimeError("MT 輸出為空或仍含 CJK 殘留")
            if not _ASCII_LETTER_RE.search(result):          # 純標點/符號輸出＝假成功
                raise RuntimeError("MT 輸出不含任何 ASCII 字母")
            return TranslationResult(translated_q=result, lang="zh", mt_model=self.mt_model_name)
        except Exception:
            # 結構化 log：不含 query 原文（D-M 脫敏精神）；Phase 9 接 LangFuse 掛 trace attribute
            logger.warning("mt_failed：以原文編碼、translated_q=null（DL-020）",
                           exc_info=True, extra={"mt_failed": True})
            return TranslationResult(translated_q=None, lang="zh", mt_model=self.mt_model_name)


def build_marian_translator(
    model_name: str = DEFAULT_MT_MODEL, glossary_path: str | Path | None = None
) -> LocalTranslator:
    """真實 MT 工廠（CPU、greedy）。重依賴在此 lazy import（gpu extra 才有）。"""
    import opencc
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)   # CPU、fp32（312MB）
    model.eval()
    cc = opencc.OpenCC("t2s")

    @torch.no_grad()
    def mt(texts: list[str]) -> list[str]:
        batch = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=128)
        gen = model.generate(**batch, num_beams=1, max_new_tokens=64)
        return tokenizer.batch_decode(gen, skip_special_tokens=True)

    return LocalTranslator(
        mt_fn=mt, mt_model_name=model_name,
        glossary=load_glossary(glossary_path), t2s_fn=cc.convert,
    )
