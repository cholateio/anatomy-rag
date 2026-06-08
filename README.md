# anatomy-rag — 解剖學科多模態 RAG 問答系統

本專案為單一醫學系內部使用的多模態（圖＋文）RAG 系統：學生以中英文提問，系統透過視覺檢索匹配解剖學教科書頁面，再由雲端 LLM 基於檢索結果產生**帶強制引文**的串流回答；教材範圍外的提問統一回覆「教材中查無此項」，不編造內容。所有問答均附書名簡寫、頁碼、圖號引文，並顯示教育用途浮水印。

> **權威規格**：`docs/ARCHITECTURE.md`（附錄 B 含完整目錄結構）及 `docs/decisions.md`（決策日誌）是本系統的唯一設計來源。實作前請先閱讀這兩份文件；任何設計變更需先在 `docs/decisions.md` 新增 `PROPOSED` 提案，經審核為 `APPROVED` 後方可實作。

---

## 架構速覽

系統分為兩條獨立路徑：

**離線批次建庫**（Offline Ingest Pipeline）
PDF → Docling 解析（Markdown＋圖片切塊）→ ColPali 視覺多向量編碼 → 二值化壓縮 → 寫入 PostgreSQL＋pgvector；整條管線不呼叫任何雲端 LLM API。

**線上推理**（Online Inference）
使用者查詢 → ColPali Encoder 微服務（獨立 GPU 容器，後端透過 HTTP `/encode_query` 呼叫）→ Stage A HNSW 粗排 → Stage B MaxSim 精排 → LLM（OpenAI 標準付費 API）串流生成 → SSE 回傳前端。

離線端與線上端共用 `shared/src/anatomy_shared/binary.py` 的二值化函式，確保索引與查詢的向量空間一致。

---

## 快速開始（TL;DR）

完整安裝與設定步驟請見 **`SETUP.md`**。最小煙霧測試流程：

```bash
cp .env.example .env
make up
make migrate
curl localhost:8000/healthz   # 預期 {"status":"ok"}
```

> **注意**：開發環境預設啟用 mock（encoder / LLM / auth），不需要 GPU 或 OpenAI API 金鑰即可完成基礎設施煙霧測試。真實端對端 `/chat` 功能將於 Phase 8 完成。`make up`、`make migrate` 及 `SETUP.md` 由後續 Phase 0 任務產出，目前尚不存在。

---

## 目錄結構

詳細目錄結構與各模組說明請參閱 `docs/ARCHITECTURE.md` **附錄 B**。

頂層概覽：

```
anatomy-rag/
├── docs/              # 系統藍圖（權威 spec）
├── shared/            # 離線端與線上端共用程式碼（binary.py 等）
├── backend/           # FastAPI 後端（/chat /healthz /warmup）
├── colpali_service/   # 獨立 GPU 微服務（:8001 /encode_query）
├── ingest/            # 離線建庫 CLI
├── frontend/          # Next.js 前端
├── eval/              # RAGAS 評估工具
├── infra/             # Docker Compose、PgBouncer、Prometheus 設定
└── tests/golden_qa.jsonl
```
