# 黃金題庫貢獻指南（§7.2）

本文件說明如何為 `tests/golden_qa.jsonl` 新增教師題目，
以達到真實 RAGAS gate 的就緒標準（≥110 題，各類別達最低數量）。

---

## Schema

每行為一個 JSON 物件，欄位如下：

| 欄位 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `id` | string | 是 | 唯一識別碼（建議格式：`<category-prefix>-<author>-<seq>`） |
| `category` | string | 是 | 見下方「題目類別」說明 |
| `query` | string | 是 | 學生可能提出的問題（支援中英文混合） |
| `expected_pages` | string[] | 視類別 | 正確答案所在頁（`book:page` 格式，須為真實 ingest 頁碼） |
| `expected_concepts` | string[] | 否 | 答案應包含的關鍵概念 |
| `metadata_filter` | object\|null | 否 | 可選的系統篩選條件 |
| `expected_response_type` | string\|null | 視類別 | `out_of_scope` 題固定填 `"教材中查無此項"` |

---

## 題目類別與各類最少數量

真實 RAGAS gate 就緒需達 **≥110 題**，且各類別達到以下最低數量：

| 類別 | 說明 | 最少 |
|------|------|------|
| `text_only` | 純文字問答，答案在教材文字段落 | 30 |
| `figure_id` | 需對應圖表，答案含圖號 | 30 |
| `cross_page` | 答案跨越多頁（需帶 ≥2 個 `expected_pages`） | 20 |
| `clinical_correlation` | 臨床相關情境問題 | 20 |
| `out_of_scope` | 教材範圍外（答案固定為「教材中查無此項」） | 10 |

---

## 頁碼格式

`expected_pages` 的每個項目格式為 `book:page`，例如 `gray42:812`。

- `book`：教科書簡寫（如 `gray42`、`netter8`）
- `page`：須為 **真實 ingest 進資料庫的頁碼**（以 Docling 解析後的頁碼為準）
- 錯誤頁碼會導致 recall 評估失真，貢獻前請以 ingest 工具確認

---

## 重要規則

1. **無 `should_refuse` 類別**：系統設計不拒答臨床問題；安全網是「引文強制 + 教育浮水印」。`should_refuse` 出現即 schema 驗證失敗。
2. **`out_of_scope` 測「查無此項」**：此類題不帶 `expected_pages`，`expected_response_type` 固定填 `"教材中查無此項"`。
3. **唯一 `id`**：重複 id 會被 schema 驗證拒絕。
4. **多標註者 kappa < 0.7 須重寫**：若兩位教師對同一題的類別判斷不一致（Cohen's kappa < 0.7），該題須討論後重寫。

---

## 真實 RAGAS Gate 啟用條件

目前 Phase 11 框架階段（DL-028），RAGAS 品質 gate 為 **`workflow_dispatch`**（手動觸發），需滿足：
- `EVAL_OPENAI_KEY`（付費 API key）已設定
- `tests/golden_qa.jsonl` 已達 ≥110 題且各類別達最低數量
- readiness 檢查通過（`golden_readiness()["ready"] == True`）

**未達 110 題前**，CI offline gate 仍跑（schema/recall/kappa/retention 等），但 readiness 為 **warning 不阻 merge**。
教師填滿後由人工將 workflow_dispatch gate 升為 hard gate（需修改 `.github/CODEOWNERS` 保護的設定）。

---

## 貢獻步驟

1. 在 `tests/golden_qa.jsonl` 末尾以 JSONL 格式新增題目
2. 執行 `cd eval && uv run --no-sync pytest tests/test_golden_schema.py -v` 確認 schema 驗證通過
3. 送 PR；`tests/golden_qa.jsonl` 受 `.github/CODEOWNERS` 保護，需 `@cholateio` 審核
4. PR 說明填寫新題類別分佈與 readiness 狀態（`golden_readiness()` 輸出）
