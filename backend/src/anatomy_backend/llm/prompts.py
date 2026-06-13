"""版本化 system prompt 與 user-message builder（§5.4 / §5.5 / §5.9 DL-021）。

MUST：system prompt 用版本化常數（不可雜在程式碼）。
MUST NOT：加入「拒答臨床問題」硬性規則（使用者皆醫學相關科系學生；安全網＝
引文強制 + 教育用途浮水印 + 回饋，不是拒答）。
"""
from __future__ import annotations

# 行為準則為靜態常數；動態的教科書摘錄/頁面圖像/使用者問題由 build_user_text() 與
# build_chat_messages() 組進 user message（§5.5 權威訊息結構）。
SYSTEM_PROMPT_V1 = """你是一位協助醫學系學生學習解剖學的助教。使用者皆為醫學相關科系學生，具備基本醫學素養。

【行為準則】
1. 僅能基於下方提供的「教科書摘錄」與「教科書頁面圖像」回答。
2. 若提供的資料不足以回答，明確說「教材中查無此項」，不得編造。
3. 每一項事實後面都必須附帶引文，格式為 [書名簡寫, 頁碼, 圖號（若有）]，
   例如：肱二頭肌起於肩胛骨喙突 [Gray42, p.812, Fig.7-23]。
4. 回答風格：簡潔、條列、優先使用教科書原文用語。可包含教科書中的臨床
   correlation（如手術解剖、神經損傷風險、病理機轉），但不主動延伸至診斷
   或治療建議；如使用者明確要求，可在引文範圍內回答。"""

SYSTEM_PROMPTS: dict[str, str] = {"v1": SYSTEM_PROMPT_V1}
ACTIVE_SYSTEM_PROMPT_VERSION = "v1"


def get_system_prompt(version: str | None = None) -> str:
    """取版本化 system prompt；version=None → active 版本。未知版本 → KeyError。"""
    return SYSTEM_PROMPTS[version or ACTIVE_SYSTEM_PROMPT_VERSION]


def build_user_text(
    text_context: str,
    user_query: str,
    prev_query: str | None = None,
) -> str:
    """組 user message 文字（§5.5）。

    追問（DL-021 §5.9）：prev_query 不為 None 時，【使用者問題】帶「前一問／當前追問」，
    **MUST NOT** 帶歷史回答或先前檢索內容（本函式無此參數，結構上不可能帶入）。
    text_context 為**本回合**檢索摘錄，非先前回合內容。
    """
    if prev_query is not None:
        question_block = f"前一問：{prev_query}\n當前追問：{user_query}"
    else:
        question_block = user_query
    return f"【教科書摘錄】\n{text_context}\n\n【使用者問題】\n{question_block}"
