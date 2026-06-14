"""Streamlit 抽檢工具薄 shell（§7.4）。

業務邏輯全在 review_loader.py（sample_logs / export_annotations）
與 regression.py（promote_cases）；本檔僅負責 UI 組裝。

執行方式：
    uv run --extra review --no-sync streamlit run \
        eval/src/anatomy_eval/review_tool.py

必要套件（eval[review] extra）：streamlit>=1.35。

注意：本模組在 import 時**不呼叫任何 Streamlit API**（僅 import streamlit as st）；
      Streamlit API 呼叫全部在 main() 內，由 ``if __name__ == "__main__"`` 或
      ``streamlit run`` 觸發，確保 ``import anatomy_eval.review_tool`` 不爆炸。
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from anatomy_eval.regression import promote_cases
from anatomy_eval.review_loader import VALID_LABELS, export_annotations, sample_logs

# ── §7.4 流程說明 ────────────────────────────────────────────────────────────
_FLOW_NOTE = """
**§7.4 人工抽檢流程**

1. 指定推理日誌 JSONL 路徑（每列含 `query`、`answer`、`sources` 等欄位）。
2. 輸入抽樣數量與隨機種子，點「載入並抽樣」。
3. 對每筆回答標注 **正確 / 部分正確 / 錯誤**，並填寫備註。
4. 點「匯出標注」儲存標注至 JSONL。
5. （可選）將錯誤案例「促進為回歸題」加入 regression 黃金題庫。

> *臨床相關（`clinical_flavored=true`）題目優先顯示於列表頂端。*
"""

_LABEL_OPTIONS = sorted(VALID_LABELS)  # ["correct", "partial", "wrong"]
_LABEL_ZH = {"correct": "正確", "partial": "部分正確", "wrong": "錯誤"}


def main() -> None:
    """Streamlit 應用程式入口（由 streamlit run 或 __main__ 呼叫）。"""
    st.set_page_config(page_title="解剖 RAG 抽檢工具", page_icon="🔬", layout="wide")
    st.title("解剖 RAG 人工抽檢工具")
    st.info(_FLOW_NOTE)

    # ── 日誌載入設定 ─────────────────────────────────────────────────────────
    with st.form("load_form"):
        log_path_str = st.text_input(
            "推理日誌 JSONL 路徑",
            placeholder="/path/to/inference_logs.jsonl",
        )
        col1, col2 = st.columns(2)
        n_sample = col1.number_input("抽樣數量", min_value=1, max_value=500, value=20)
        seed = col2.number_input("隨機種子", min_value=0, value=42)
        load_clicked = st.form_submit_button("載入並抽樣")

    if load_clicked:
        p = Path(log_path_str.strip())
        if not p.exists():
            st.error(f"找不到檔案：{p}")
        else:
            rows: list[dict] = []
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        st.warning(f"跳過無效 JSON 行：{exc}")
            sampled = sample_logs(rows, int(n_sample), seed=int(seed))
            st.session_state["sampled"] = sampled
            # 初始化標注（每筆預設 correct，空備註）
            st.session_state["annotations"] = [
                {"label": "correct", "comment": ""} for _ in sampled
            ]
            st.success(f"已抽樣 {len(sampled)} 筆（日誌共 {len(rows)} 筆）")

    # ── 標注介面 ─────────────────────────────────────────────────────────────
    sampled: list[dict] = st.session_state.get("sampled", [])
    annotations: list[dict] = st.session_state.get("annotations", [])

    if sampled:
        st.divider()
        st.subheader("標注")

        for i, (row, ann) in enumerate(zip(sampled, annotations, strict=False)):
            tag = "🏥 臨床" if row.get("clinical_flavored") else ""
            preview = str(row.get("query", ""))[:80]
            with st.expander(f"#{i + 1} {tag}  {preview}", expanded=(i == 0)):
                st.markdown(f"**問題：** {row.get('query', '—')}")
                st.markdown(f"**回答：** {row.get('answer', '—')}")
                sources = row.get("sources", row.get("retrieved_pages", []))
                if sources:
                    st.markdown("**來源：** " + "、".join(str(s) for s in sources))

                current_label = ann.get("label", "correct")
                idx = _LABEL_OPTIONS.index(current_label) if current_label in _LABEL_OPTIONS else 0
                chosen = st.radio(
                    "標注",
                    options=_LABEL_OPTIONS,
                    format_func=lambda x: f"{x}（{_LABEL_ZH[x]}）",
                    index=idx,
                    horizontal=True,
                    key=f"label_{i}",
                )
                comment = st.text_input(
                    "備註",
                    value=ann.get("comment", ""),
                    key=f"comment_{i}",
                )
                annotations[i] = {**row, "label": chosen, "comment": comment}

        st.session_state["annotations"] = annotations

        # ── 匯出 ──────────────────────────────────────────────────────────────
        st.divider()
        col_a, col_b = st.columns([3, 1])
        export_path = col_a.text_input("匯出路徑", value="annotations_export.jsonl")
        if col_b.button("匯出標注", use_container_width=True):
            try:
                export_annotations(annotations, export_path)
                st.success(f"已匯出 {len(annotations)} 筆標注至 {export_path}")
            except ValueError as exc:
                st.error(f"匯出失敗：{exc}")

        # ── 促進為回歸題（可選）───────────────────────────────────────────────
        st.divider()
        st.subheader("促進為回歸題（可選）")
        st.caption(
            "將標注為「錯誤」的案例促進至 regression 黃金題庫（案例需含完整 schema 欄位）。"
        )
        col_r, col_rb = st.columns([3, 1])
        regression_path = col_r.text_input(
            "Regression JSONL 路徑",
            value="tests/regression_qa.jsonl",
        )
        if col_rb.button("促進錯誤案例為回歸題", use_container_width=True):
            wrong_cases = [a for a in annotations if a.get("label") == "wrong"]
            if not wrong_cases:
                st.info("目前沒有標注為「錯誤」的案例。")
            else:
                try:
                    added = promote_cases(wrong_cases, regression_path)
                    st.success(f"已促進 {added} 筆案例至 {regression_path}")
                except ValueError as exc:
                    st.error(f"促進失敗（schema 驗證未通過）：{exc}")


if __name__ == "__main__":
    main()
