# Phase 3 — ColPali Encoder 微服務實作計畫（真實 runtime + 本地 MT DL-020 + readiness 503 + recall gate）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Phase 0/1 留下的 mock encoder 升級為可上線的 ColPali encoder 微服務——真實
`vidore/colpali-v1.3-hf` runtime（承接 Phase 1 固定的 `EncodedVectors` 介面）、DL-020 本地
MT 查詢翻譯（opus-mt-zh-en + OpenCC 繁→簡 + 術語 glossary）、`/healthz` not-ready→503、
推理不阻塞 event loop，並以 D-P recall harness gate（含中文 query）驗收 MT→編碼→二值化全鏈路。

**Architecture:** torch 依賴隔離不變（D-L）：`shared/colpali_runtime.py` 維持 torch-free，
真實 runtime 放新檔 `shared/colpali_real.py`（`get_runtime(mock=False)` lazy import）。
colpali_service 以 `RealColPaliEncoder` 組合「翻譯（translate.py）→ ColPali 編碼 →
shared.binary 二值化/池化」，與 `MockEncoder` 共用同一 `/encode_query` 契約（§4.2）。
模型載入走 FastAPI lifespan 背景執行緒（mock 同步載入），就緒前 `/healthz`、`/encode_query`
回 503；推理經 `anyio.to_thread` + lock 序列化。encoder smoke gate 為手動 GPU 腳本（非 CI）：
渲染 16 個英文偽頁面（含 4 干擾頁）→ 真模型編碼 → zh/en query 走完整管線（含 MT）→
MaxSim + pooled cosine 雙軌排序皆過門檻。

**Tech Stack:** transformers **5.10.x（pin `>=5,<6`）**、torch 2.11 cu128（容器內 reinstall，既有）、
`ColPaliForRetrieval`+`ColPaliProcessor`（bf16、SDPA、`dtype=` 參數）、MarianMT
`Helsinki-NLP/opus-mt-zh-en`（CPU、greedy）、**新依賴：`sentencepiece`、`OpenCC`、`sacremoses`**、
pillow（既有 colpali extra）、asgi-lifespan（既有 dev）。

---

## 0. 設計定案與研究結論（本計畫內生效；Task 1 寫入治理文件）

### 0.1 ⚠️ 需使用者核准後才可開工（依 2026-06-08/10 授權範圍，新依賴必問）

> **阻斷前提**：下表任一項未獲核准前，Task 0 不得動工；Task 1 的治理附註中「新依賴經使用者
> 核准」一句，**以實際核准日期為準填寫**（本計畫本身不構成核准紀錄；Codex 審查 #14）。

| 項目 | 內容 | 理由 |
|---|---|---|
| 新依賴 1 | `sentencepiece>=0.2.0`（colpali_service gpu extra） | MarianTokenizer 必要；transformers 5.x 列 extras 不自帶 |
| 新依賴 2 | `OpenCC>=1.1.9`（官方 PyPI C++ binding，有 manylinux wheel） | 繁→簡前處理（opus-mt-zh-en 訓練語料以簡體為主；`opencc-python-reimplemented` 已 12 月+ 無維護，不採） |
| 新依賴 3 | `sacremoses>=0.1.1`（純 Python，極小） | MarianTokenizer 的 detokenize 建議依賴；v5 下缺席行為未確認，預防性安裝 |
| 版本約束變更 | `shared[colpali]` 的 `transformers>=4.53.1` → **`>=5,<6`** | 4.52–4.53 是 colpali-v1.3-hf 已知破損區間（vlm 重構造成 uninitialized weights，[HF discussion #6](https://huggingface.co/vidore/colpali-v1.3-hf/discussions/6)）；colpali-engine v0.3.14 官方亦遷移 `>=5,<6` |
| 磁碟/網路 | 首次 `make encoder-models` 下載 ~7–8 GB（ColPali ~6.9GB + Marian 312MB）到 named volume `hfcache` | 模型權重快取，重建 image 不需重抓 |

### 0.2 研究結論（2026-06-12 WebSearch scout；Gemini scout 因 Google API 500 改由 Claude WebSearch 執行）

- transformers 5.11 文件仍完整收錄 ColPali（範例即 colpali-v1.3-hf）與 MarianMT；v5 未移除兩者。
- v5 起 `torch_dtype` 改名 **`dtype`**（新程式碼一律用 `dtype`）；`dtype` 預設 `"auto"`。
- **lm_head 為 tied weights**：v5 載入 ColPali 時 `missing_keys` 含 `lm_head` 屬預期
  （colpali-engine 同樣忽略）；其餘 missing/unexpected/mismatched keys 必須視為錯誤 → runtime 內建守門。
- torch 下限 `>=2.4` → torch 2.11 OK；sm_120（RTX 5060 Ti）+ bf16 + SDPA 無已知負面報告
  （缺正面證據，由 Task 9 實機驗證補上）。
- opus-mt-zh-en：PyTorch 權重 312MB；CPU 短句延遲估 0.1–0.5s（無權威 benchmark，Task 9 實測）。
- `Helsinki-NLP/opus-mt-zh_TW-en` **不存在**，不要規劃使用。

### 0.3 MT 管線設計（DL-020 SHOULD 項落地；Task 1 補記 spec）

```
query ─ CJK 偵測 ─ en → identity（translated_q=原文, lang=en）
         │ zh
         ▼
   OpenCC t2s（繁→簡）
         ▼
   glossary 長詞優先替換（簡體 key → 英文術語；key 於載入時由同一 OpenCC 轉簡）
         ▼
   CJK-run / 非 CJK-run 分段：僅 CJK 段送 MarianMT（greedy, num_beams=1, max_new_tokens=64）；
   ASCII/拉丁術語段原樣保留 ── 規避「placeholder 被 sentencepiece 拆爛」的已知坑
         ▼
   以單一空白 join → translated_q（lang=zh）
   守門（Codex 審查 #6 + 複審 #1 採納）：MT 輸出數 ≠ 輸入段數 → 失敗；join 後仍含 CJK
   殘留 → 失敗；輸出不含任何 ASCII 字母（純標點）→ 失敗；
   所有格虛詞段（的/之，整段恰為單一虛詞）丟棄不送 MT——與/和/或/在/是等有語意、照送
   任一失敗 → translated_q=null、lang=zh、結構化 log（extra={"mt_failed": True}，不含 query
   原文——D-M 脫敏精神；Phase 9 接 LangFuse 時掛 trace attribute）——不阻斷編碼（spec §5.1）
```

- 編碼輸入：zh 且 MT 成功 → **以英文 translated_q 做 ColPali 編碼**（DL-020）；MT 失敗 → 原文編碼。
- MT 在 **CPU** 跑（避免與 ColPali 爭 VRAM；312MB 模型 CPU 足夠）。
- CJK 標點（？。、）落在非 CJK 段、原樣保留——對 BM25 無害，v1 不做映射（YAGNI）。
- 分段翻譯的語境破碎風險（glossary 替換後殘段如「是什麼」單獨送 MT）由 §0.6 smoke gate 的
  **非 glossary／混語題**實測把關；不達標的升級選項＝整句 MT＋事後術語校正（OPEN，依 gate 數據裁決）。

### 0.4 真實 runtime 的 valid_mask 約定（Task 1 寫入 §4.2 註記；Codex 審查 #2 部分採納）

真實 query 編碼的 `valid_mask` = processor 的 **attention_mask**（排除 batch padding）。
前綴／augmentation tokens 是否在 attention_mask 內，**以 transformers 5.10.2 實測為準**——
原則是「跟著模型原生 MaxSim 行為走」（processor 給什麼 mask 就用什麼），不自行二次裁切。
驗證手段：(a) GPU 測試斷言不同長度 query 產生不同 valid 數、valid 列全為有限非零向量；
(b) Task 9 實機 curl 印 token 數人工核對；(c) smoke gate 的檢索品質為功能性總驗收。
mock 的「排除前 2 個前綴 token」僅為 mock 自身語意；契約以「valid_mask=False ⇒ 不進
池化/二值化」為準，兩者皆符合。token 數因 query 而異，下游 **MUST NOT** 假設固定
token 數（mock 恆為 18 僅是 mock 性質）。`EncodedVectors` docstring 同步補上述語意（Task 4）。

### 0.5 Phase 3 明確不做（YAGNI）

- **不做 `modal_app.py`、不啟用任何真實 fallback encoder**（Modal=SaaS，依授權需另行核准 +
  data residency 走 decisions.md；D-O 列 optional）。§5.1「primary 失敗自動 fallback」的 MUST
  由 Phase 8 backend client 的程式邏輯滿足；dev/冒煙環境 `COLPALI_FALLBACK_URL` 可暫指 mock
  **僅供驗證切換機制，不構成 §5.1/DL-020 生產合規**——生產 fallback MUST 為內建同一 MT 模型
  的真實 encoder（DL-020「主／備契約一致」），未就緒前不得宣稱 fallback 合規（Codex 審查 #1 採納）。
- **不做** backend `encoder/client.py`（Phase 8）。
- **不做** NLLB-600M / 跨語言 encoder 升級（gate 不過才依 DL-020 升級序走 decisions.md）。
- **不做** MT 結果快取、batch encode 調優（Phase 4 ingest 時依實測加）。
- **不動** `eval_thresholds.yaml`（RAGAS 門檻，Phase 11）；gate 門檻為腳本參數（見 0.6）。

### 0.6 encoder smoke gate 設計（D-P 種子；**非** DL-013 上線品質 gate）

> 定位（Codex 審查 #5 採納）：合成偽頁面 gate 驗的是「MT→編碼→二值化→排序」管線**功能正確性**
> （與 Phase 1 harness 合成測試同一定位）；DL-013 真實教材品質 gate 屬 Phase 5/11。
> 防 author-tuning：**24 題 query 全文與 16 頁主題/必含術語已在本計畫 Task 8 完整定案**，
> implementer 不得增刪改題目。

- 手動 GPU 腳本（非 CI）：`colpali_service/scripts/encoder_gate.py`，`make encoder-gate` 執行。
- 資料：`eval/data/encoder_gate_pages.jsonl`（**16** 個英文偽頁面=12 個出題主題 + **4 個近鄰
  干擾頁**（triceps／ulnar nerve／hip capsule／inferior mediastinum），PIL 渲染成圖）+
  `eval/data/encoder_gate_queries.jsonl`（24 題=12 zh + 12 en；zh 含 **6 glossary 題、
  4 非 glossary 題、2 混語題**——分段翻譯與 MT 裸句品質都被覆蓋）。
- 排序 oracle 雙軌、**雙軌皆 gate**（Codex 審查 #4 採納）：
  1. Stage B 軌：`anatomy_eval.reference.maxsim_hamming`（tokens_bin × page patch_bin）；
  2. Stage A 軌：pooled cosine 排序（驗 DL-019 pooled_f32 品質），另斷言 pooled 無 NaN、norm>0。
- **門檻（起手值，CLI 可調；不可為過關調低，調整須附理由）**：
  MaxSim `recall@3`：en ≥ 0.9、zh ≥ 0.75；pooled cosine `recall@3`：en ≥ 0.75、zh ≥ 0.6。
  未達 exit 1。腳本印每題 zh 的 `translated_q`（人工抽查）與 encode/MT 延遲。
- fixture 結構不變量由**腳本內 fail-closed 驗證**（頁數/題數/語言配比/引用存在；Codex #13）。
- 偽頁面為自撰英文解剖短文（非教科書掃描），無版權疑慮。

---

## 1. 檔案結構地圖

```
shared/
├── pyproject.toml                                   # Task 2：transformers pin >=5,<6
├── tests/test_loading_guard.py                      # Task 4：check_loading_info 單元（torch-free）
└── src/anatomy_shared/
    ├── colpali_runtime.py                           # Task 4：get_runtime lazy import + check_loading_info（維持 torch-free）
    └── colpali_real.py                              # Task 4：RealColPaliRuntime（bf16/SDPA、共用守門）
colpali_service/
├── pyproject.toml                                   # Task 2：gpu extra += sentencepiece/OpenCC/sacremoses
├── Dockerfile                                       # Task 7：HF_HOME、fonts、eval 成員、healthcheck
├── scripts/
│   └── encoder_gate.py                              # Task 8：D-P recall gate（手動 GPU）
├── src/colpali_service/
│   ├── main.py                                      # Task 6：lifespan/503/to_thread
│   ├── encoder.py                                   # Task 5：get_encoder 真實路徑；_detect_lang 移出
│   ├── real_encoder.py                              # Task 5：RealColPaliEncoder + build_real_encoder
│   ├── translate.py                                 # Task 3：detect_lang/glossary/LocalTranslator/factory
│   └── glossary_zh_en.tsv                           # Task 3：起手 40 詞解剖術語表
└── tests/
    ├── test_encode.py                               # Task 6：改 LifespanManager；real-path 守門測試更新
    ├── test_translate_unit.py                       # Task 3：純邏輯（注入 fake MT，無 torch）
    ├── test_real_encoder_unit.py                    # Task 5：fake runtime+translator 組合（無 torch）
    ├── test_service_readiness.py                    # Task 6：503→ready（fake 慢載入）
    ├── test_real_runtime_gpu.py                     # Task 4：gpu marker（CI 自動 skip）
    └── test_marian_mt.py                            # Task 3：mt marker（env-gated，下載 312MB）
eval/data/
├── encoder_gate_pages.jsonl                         # Task 8
└── encoder_gate_queries.jsonl                       # Task 8
docker-compose.gpu.yml                               # Task 7：hfcache volume、healthcheck 放寬
Makefile                                             # Task 7：encoder-models / encoder-gate
pyproject.toml                                       # Task 2：pytest markers += gpu, mt
docs/decisions.md                                    # Task 1：DL-020 附註
docs/ARCHITECTURE.md                                 # Task 1：§5.1 OpenCC/分段註記、§4.2 valid_mask 註記
SETUP.md                                             # Task 9：GPU encoder 啟用段
```

測試檔名維持**全域唯一**；執行模式：**subagent-driven**（implementer=Sonnet、TDD；任務間主模型審查；
終審=Codex 跨模型）。CI unit job 無 torch：gpu/mt 測試以 `importorskip` + marker 自動 skip。
本機 GPU 驗收（Task 9）在使用者機器（WSL2 + RTX 5060 Ti）執行。

---

### Task 0: 開分支

- [ ] **Step 0.1**

```bash
git checkout -b feat/phase-3-encoder-service
```

### Task 1: 治理更新（decisions.md + ARCHITECTURE.md）

**Files:**
- Modify: `docs/decisions.md`（DL-020 條目尾端）
- Modify: `docs/ARCHITECTURE.md`（§5.1 DL-020 區塊、§4.2）

- [ ] **Step 1.1: decisions.md DL-020 條目尾端追加實作附註**

```markdown
> **實作附註（2026-06-12，Phase 3 落地；APPROVED（委派），使用者保留否決權）**：
> (a) MT 前處理加 **OpenCC `t2s` 繁→簡**（opus-mt-zh-en 訓練語料以簡體為主；OpenCC 為本地
> C++ binding，零 API 成本，符合 MUST NOT 雲端翻譯）；(b) SHOULD 的「ASCII/拉丁術語 span
> 保護」以 **CJK-run 分段翻譯**實現（僅 CJK 段送 MT，非 CJK 段原樣保留），規避 placeholder
> 被 sentencepiece 拆壞的已知問題；MT 輸出段數不符或輸出仍含 CJK 一律視為失敗（translated_q=null）；
> (c) glossary 起手 40 詞（`colpali_service/glossary_zh_en.tsv`，繁體 key、載入時轉簡、長詞優先）；
> (d) 新依賴 sentencepiece/OpenCC/sacremoses 經使用者核准（日期依實際核准日填寫，紀錄見 PR）；
> (e) transformers pin `>=5,<6`（4.52–4.53 為 colpali-v1.3-hf 已知破損區間）。
```

- [ ] **Step 1.2: ARCHITECTURE.md §5.1 DL-020 區塊，「DECIDED 起手引擎」bullet 後插入**

```markdown
- 中文 query 於 MT 前先以 **OpenCC `t2s`** 轉簡體（訓練語料偏簡體；本地轉換、零成本）；
  ASCII/拉丁術語 span 保護以 **CJK-run 分段**實現——僅 CJK 段送 MT，其餘原樣保留。
```

- [ ] **Step 1.3: ARCHITECTURE.md §4.2 末尾追加 valid_mask / token 數註記**

```markdown
- 真實 encoder 的 token 有效性以 processor attention_mask 為準（排除 batch padding；
  前綴/augmentation tokens 是否納入跟隨模型原生 MaxSim 行為，不二次裁切）。`tokens_bin`
  數量隨 query 而異，下游 **MUST NOT** 假設固定 token 數。
```

- [ ] **Step 1.4: Commit**

```bash
git add docs/decisions.md docs/ARCHITECTURE.md
git commit -m "docs(phase-3): DL-020 實作附註（OpenCC/分段翻譯/新依賴/transformers pin）+ §4.2 valid_mask 註記"
```

### Task 2: 依賴與測試 markers

**Files:**
- Modify: `shared/pyproject.toml:8-12`
- Modify: `colpali_service/pyproject.toml:7-9`
- Modify: `pyproject.toml:28-31`（markers）

- [ ] **Step 2.1: shared colpali extra pin transformers**

```toml
[project.optional-dependencies]
colpali = [
  "torch>=2.6",
  "transformers>=5,<6",   # 4.52–4.53 對 colpali-v1.3-hf 有 uninitialized-weights 已知破損；v5 為官方維護路徑
  "pillow>=10",
]
```

- [ ] **Step 2.2: colpali_service gpu extra 加 MT 依賴（並顯式宣告 anyio——main.py 直接 import）**

```toml
dependencies = ["anatomy-shared", "fastapi>=0.115", "uvicorn[standard]>=0.34",
                "pydantic>=2.7", "pydantic-settings>=2.3", "numpy>=1.26", "anyio>=4"]
[project.optional-dependencies]
# 真實 ColPali + 本地 MT（DL-020）；mock/CPU 路徑不需，故全在 extra
gpu = [
  "anatomy-shared[colpali]",
  "sentencepiece>=0.2.0",   # MarianTokenizer 必要
  "OpenCC>=1.1.9",          # 繁→簡 t2s（官方 PyPI binding；reimplemented 版已無維護）
  "sacremoses>=0.1.1",      # Marian detokenize 建議依賴
]
modal = ["modal>=0.64"]
```

- [ ] **Step 2.3: root pyproject markers 追加**

```toml
markers = [
  "db: 需要 PostgreSQL 連線的測試（CI db-integration job 才跑；Phase 2 起）",
  "integration: 跨模組整合測試（可能需 DB/Redis/encoder）",
  "gpu: 需要 CUDA + gpu extra（torch/transformers）的測試；CI 無 torch 自動 skip（Phase 3 起）",
  "mt: 需下載真實 MarianMT 模型（312MB）的測試；RUN_MT_TESTS=1 才跑（Phase 3 起）",
]
```

- [ ] **Step 2.4: 重新鎖定 + 驗證 unit 路徑不拉 torch**

```bash
uv lock
uv sync --group dev && uv sync --package colpali-service --inexact
uv run --no-sync python -c "import anatomy_shared.colpali_runtime, sys; assert 'torch' not in sys.modules; print('torch-free OK')"
```

Expected: `torch-free OK`（gpu extra 未裝，colpali-service base 不變重）

- [ ] **Step 2.5: Commit**

```bash
git add shared/pyproject.toml colpali_service/pyproject.toml pyproject.toml uv.lock
git commit -m "build(phase-3): transformers pin >=5,<6、gpu extra 加 sentencepiece/OpenCC/sacremoses、gpu/mt markers"
```

### Task 3: translate.py（DL-020 翻譯管線；TDD、無 torch）

**Files:**
- Create: `colpali_service/src/colpali_service/translate.py`
- Create: `colpali_service/src/colpali_service/glossary_zh_en.tsv`
- Test: `colpali_service/tests/test_translate_unit.py`
- Test: `colpali_service/tests/test_marian_mt.py`（mt marker）

- [ ] **Step 3.1: 寫失敗測試（純邏輯；fake MT/t2s 注入，無 torch）**

```python
"""translate.py 純邏輯測試：CJK 偵測 / glossary / 分段 / 失敗 fallback（DL-020）。

fake mt_fn / t2s_fn 注入，無 torch、無模型下載；真實 Marian 在 test_marian_mt.py（mt marker）。
"""
import pytest
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
    # 有語意的虛詞（與）照送 MT（Codex 複審 #1：不可丟 或/在/是/與 等）
    tr = _fake_translator(glossary=[("心脏", "heart")])
    r = tr.translate("biceps brachii 與心臟的位置")
    assert r.lang == "zh"
    assert "biceps brachii" in r.translated_q          # ASCII span 原樣保留
    assert "heart" in r.translated_q                   # glossary 在 t2s 之後命中
    assert "MT-SEG" in r.translated_q                  # 殘餘 CJK 段（與、的位置）送了 MT
    assert "心" not in r.translated_q                  # CJK 不殘留


def test_punctuation_only_output_is_failure():
    """全部段被丟棄/輸出只剩標點 → 不得宣稱翻譯成功（Codex 複審 #1）。"""
    tr = _fake_translator(mt_fn=lambda texts: ["?"] * len(texts))
    assert tr.translate("的？").translated_q is None


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
```

- [ ] **Step 3.2: 跑測試確認失敗**

```bash
uv run --no-sync pytest colpali_service/tests/test_translate_unit.py -q
```

Expected: FAIL（`ModuleNotFoundError: colpali_service.translate`）

- [ ] **Step 3.3: 實作 translate.py**

```python
"""DL-020 本地查詢翻譯（zh/混語 → en）。

管線：CJK 偵測 → OpenCC t2s（繁→簡）→ glossary 長詞優先替換（出英文術語）→
CJK-run 分段、僅 CJK 段送 MarianMT（greedy）→ 空白 join。
非 CJK 段（ASCII/拉丁術語、數字、標點）原樣保留——「span 保護」不用 placeholder
（sentencepiece 會拆壞 placeholder），用分段繞過。
任一步例外 → translated_q=None（§5.1：MT 失敗不阻斷查詢）。
本模組 import 必須 torch-free；重依賴只在 build_marian_translator() 內 lazy import。
"""
import logging
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")          # 原 encoder.py 之單一來源（Phase 3 移入）
_CJK_RUN_RE = re.compile(r"([㐀-䶿一-鿿]+)")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
# glossary 替換後殘留的「所有格/連接」虛詞段：直接丟棄不送 MT。
# 僅列語意可安全省略者（的/之）；與/和/或/在/是等有語意，照送 MT（Codex 複審 #1）。
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
            raise ValueError(f"glossary 第 {lineno} 行格式錯誤（須為 term\\ttranslation）：{line!r}")
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
                for i, t in zip(cjk_idx, translated):
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
```

- [ ] **Step 3.4: 建立 glossary_zh_en.tsv（起手 40 詞；繁體 key）**

```tsv
# 解剖術語 glossary（DL-020 SHOULD）：term<TAB>translation；長詞優先於載入時排序
# key 為繁體；LocalTranslator 載入時以 OpenCC t2s 轉簡比對
肱二頭肌	biceps brachii
肱三頭肌	triceps brachii
三角肌	deltoid
胸大肌	pectoralis major
斜方肌	trapezius
背闊肌	latissimus dorsi
縫匠肌	sartorius
股四頭肌	quadriceps femoris
腓腸肌	gastrocnemius
橫膈膜	diaphragm
正中神經	median nerve
尺神經	ulnar nerve
橈神經	radial nerve
坐骨神經	sciatic nerve
股神經	femoral nerve
迷走神經	vagus nerve
臂神經叢	brachial plexus
腰神經叢	lumbar plexus
神經支配	innervation
頸動脈	carotid artery
主動脈弓	aortic arch
鎖骨下動脈	subclavian artery
股動脈	femoral artery
旋股內側動脈	medial circumflex femoral artery
血液供應	blood supply
肩胛骨	scapula
鎖骨	clavicle
肱骨	humerus
橈骨	radius
尺骨	ulna
股骨頭	femoral head
股骨	femur
脛骨	tibia
腓骨	fibula
喙突	coracoid process
起止點	origin and insertion
上縱隔	superior mediastinum
縱隔	mediastinum
腹股溝管	inguinal canal
膝關節	knee joint
```

註：TSV 需確保分隔為單一 tab 字元。`pyproject.toml` 的 hatch wheel 設定已含整個
`src/colpali_service` 目錄，`.tsv` 會隨套件打包（`importlib.resources` 讀取）。

- [ ] **Step 3.5: 跑測試確認通過**

```bash
uv run --no-sync pytest colpali_service/tests/test_translate_unit.py -q
```

Expected: PASS（12 tests）

- [ ] **Step 3.6: 真實 Marian 整合測試（mt marker；CI/預設 skip）**

```python
"""真實 MarianMT 整合（mt marker）：RUN_MT_TESTS=1 才跑（下載 312MB 模型）。
手動：RUN_MT_TESTS=1 uv run --no-sync pytest colpali_service/tests/test_marian_mt.py -q
（需先 uv sync --package colpali-service --extra gpu --inexact）"""
import os

import pytest

pytest.importorskip("sentencepiece")
pytest.importorskip("opencc")
pytestmark = [
    pytest.mark.mt,
    pytest.mark.skipif(os.environ.get("RUN_MT_TESTS") != "1", reason="需 RUN_MT_TESTS=1（下載模型）"),
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
```

- [ ] **Step 3.7: Commit**

```bash
git add colpali_service/src/colpali_service/translate.py colpali_service/src/colpali_service/glossary_zh_en.tsv colpali_service/tests/test_translate_unit.py colpali_service/tests/test_marian_mt.py
git commit -m "feat(phase-3): DL-020 本地 MT 翻譯管線（OpenCC t2s + glossary + CJK 分段 + 失敗 fallback）"
```

### Task 4: 真實 ColPali runtime（shared/colpali_real.py）

**Files:**
- Create: `shared/src/anatomy_shared/colpali_real.py`
- Modify: `shared/src/anatomy_shared/colpali_runtime.py`（get_runtime、check_loading_info、EncodedVectors docstring）
- Test: `shared/tests/test_loading_guard.py`（torch-free 單元）
- Test: `colpali_service/tests/test_real_runtime_gpu.py`（gpu marker）

- [ ] **Step 4.0: 載入守門的 torch-free 單元測試（先寫、先紅）**

`shared/tests/test_loading_guard.py`：

```python
"""check_loading_info 守門（torch-free 單元；Codex 審查 #9——精確 allowlist + error_msgs）。"""
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
```

執行 `uv run --no-sync pytest shared/tests/test_loading_guard.py -q` → Expected: FAIL（函式不存在）

- [ ] **Step 4.0b: 在 colpali_runtime.py（torch-free）加守門函式 + EncodedVectors docstring 補語意**

```python
# 預期 tied-weights 缺失 key（lm_head 與 embed_tokens 權重綁定；colpali-engine v0.3.14 同樣忽略）。
# 精確 allowlist 而非子字串比對——避免吞掉真實壞損（Codex 審查 #9）。
EXPECTED_TIED_MISSING_KEYS = frozenset({
    "lm_head.weight", "model.lm_head.weight", "vlm.lm_head.weight",
})


def check_loading_info(loading_info: dict,
                       expected_missing: frozenset = EXPECTED_TIED_MISSING_KEYS) -> None:
    """`from_pretrained(output_loading_info=True)` 結果守門——
    「載入無 uninitialized weights 警告」（roadmap Phase 3 AC）的程式化版本。

    除精確列名的預期 tied weights 外，任何 missing/unexpected/mismatched/error_msgs
    一律 RuntimeError fail-fast。純 dict 驗證、torch-free，供單元測試直測。
    """
    missing = [k for k in loading_info.get("missing_keys", []) if k not in expected_missing]
    unexpected = list(loading_info.get("unexpected_keys", []))
    mismatched = list(loading_info.get("mismatched_keys", []))
    errors = list(loading_info.get("error_msgs", []))
    if missing or unexpected or mismatched or errors:
        raise RuntimeError(
            "ColPali 權重載入異常（uninitialized weights 守門）："
            f"missing={missing} unexpected={unexpected} mismatched={mismatched} errors={errors}"
        )
```

`EncodedVectors` docstring 追加一行（§0.4 語意）：

```python
    真實 runtime：valid_mask=attention_mask（排除 batch padding；前綴/augmentation tokens
    是否納入跟隨 processor 原生行為）。token 數隨輸入而異，呼叫端不得假設固定數量。
```

執行 Step 4.0 測試 → Expected: PASS；並確認 torch-free：
`uv run --no-sync python -c "import anatomy_shared.colpali_runtime, sys; assert 'torch' not in sys.modules"`

- [ ] **Step 4.1: 寫 gpu 測試（CI 無 torch 自動 skip）**

```python
"""真實 ColPali runtime（gpu marker）：需 CUDA + gpu extra；CI 自動 skip。
手動（GPU 容器內）：見 Makefile encoder-gate / SETUP.md。"""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not torch.cuda.is_available(), reason="需 CUDA"),
]


@pytest.fixture(scope="module")
def runtime():
    from anatomy_shared.colpali_runtime import get_runtime

    return get_runtime(mock=False)   # 載入即執行 loading_info 守門


def test_query_shapes_and_mask(runtime):
    enc = runtime.encode_query("origin and insertion of biceps brachii")
    assert enc.embeddings.ndim == 2 and enc.embeddings.shape[1] == 128
    assert enc.embeddings.dtype == np.float32
    assert enc.valid_mask.dtype == bool and enc.valid_mask.shape == (enc.embeddings.shape[0],)
    assert enc.valid_mask.sum() >= 4     # 至少含實際 query tokens


def test_page_encode_smoke(runtime):
    from PIL import Image

    img = Image.new("RGB", (448, 448), "white")
    enc = runtime.encode_page(img)
    assert enc.embeddings.shape[1] == 128 and enc.valid_mask.all()  # 影像 patch 無 padding
    assert enc.embeddings.shape[0] >= 256   # 約 1024 patch（依 processor 解析度）


def test_same_query_twice_same_shape_close_values(runtime):
    a = runtime.encode_query("median nerve")
    b = runtime.encode_query("median nerve")
    assert a.embeddings.shape == b.embeddings.shape
    # eval+no_grad 下同輸入應幾乎一致（bf16 → fp32 容差）
    assert np.allclose(a.embeddings, b.embeddings, atol=5e-2)


def test_torch_and_transformers_are_validated_versions():
    """D-S/Codex #11+複審 #4：torch/transformers 必須是本 phase 驗證過的組合。
    lock 升版（如 transformers 5.11）會讓本測試紅 → 強制重跑 GPU 驗證後才能更新斷言。"""
    import transformers

    assert torch.__version__.startswith("2.11"), torch.__version__
    assert torch.version.cuda and torch.version.cuda.startswith("12.8"), torch.version.cuda
    assert transformers.__version__.startswith("5.10"), transformers.__version__


def test_different_query_lengths_yield_different_valid_counts(runtime):
    """§0.4 功能性驗證：valid 數隨 query 長度變動（mask 不是壞掉的常數）。"""
    short = runtime.encode_query("median nerve")
    long = runtime.encode_query(
        "course and branches of the median nerve in the forearm and the hand")
    assert long.valid_mask.sum() > short.valid_mask.sum()


def test_valid_rows_are_finite_and_nonzero(runtime):
    enc = runtime.encode_query("brachial plexus")
    valid = enc.embeddings[enc.valid_mask]
    assert np.isfinite(valid).all()
    assert (np.linalg.norm(valid, axis=1) > 0).all()
```

- [ ] **Step 4.2: 實作 colpali_real.py**

```python
"""真實 ColPali runtime（Phase 3）——import 本模組即拉 torch/transformers。

僅供 GPU 路徑（gpu extra）；torch-free 的介面/mock 在 colpali_runtime.py（D-L）。
transformers pin >=5,<6：v5 用 `dtype=`（torch_dtype 已改名）；lm_head 為 tied weights，
載入時列 missing 屬預期（colpali-engine v0.3.14 同樣忽略），其餘缺漏一律 fail-fast——
這就是 roadmap「載入無 uninitialized weights 警告」的程式化守門。
"""
import logging

import numpy as np
import torch
from transformers import ColPaliForRetrieval, ColPaliProcessor

from anatomy_shared.colpali_runtime import EncodedVectors, check_loading_info

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "vidore/colpali-v1.3-hf"   # DECIDED（§2.3）


class RealColPaliRuntime:
    """ColPaliForRetrieval + ColPaliProcessor（bf16、SDPA）；輸出 EncodedVectors（fp32）。

    valid_mask = processor attention_mask（排除 batch padding；前綴/augmentation tokens
    是否納入跟隨 processor 原生行為，不二次裁切——見 ARCHITECTURE §4.2 註記）。
    """

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, device: str = "cuda",
                 dtype: torch.dtype = torch.bfloat16):
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(
                "COLPALI_DEVICE=cuda 但 CUDA 不可用——§5.1 MUST NOT 靜默 CPU fallback；"
                "請檢查 nvidia-container-toolkit / make gpu-smoke"
            )
        self.model_id = model_id
        model, loading_info = ColPaliForRetrieval.from_pretrained(
            model_id, dtype=dtype, attn_implementation="sdpa", output_loading_info=True,
        )
        check_loading_info(loading_info)   # 共用守門（torch-free、已單元測試；Codex #9）
        self._model = model.to(device).eval()
        self._processor = ColPaliProcessor.from_pretrained(model_id)
        self._device = device
        logger.info("ColPali 載入完成：%s（device=%s, dtype=%s）", model_id, device, dtype)

    @torch.no_grad()
    def encode_query(self, q: str) -> EncodedVectors:
        batch = self._processor(text=[q], return_tensors="pt").to(self._device)
        out = self._model(**batch)
        emb = out.embeddings[0].to(torch.float32).cpu().numpy()
        mask = batch["attention_mask"][0].bool().cpu().numpy()
        return EncodedVectors(embeddings=emb, valid_mask=mask)

    @torch.no_grad()
    def encode_page(self, image) -> EncodedVectors:
        return self.encode_pages([image])[0]

    @torch.no_grad()
    def encode_pages(self, images, batch_size: int = 4) -> list[EncodedVectors]:
        """批次頁面編碼（§2.3 SHOULD；Phase 4 ingest 主要入口）。"""
        results: list[EncodedVectors] = []
        images = list(images)
        for i in range(0, len(images), batch_size):
            batch = self._processor(images=images[i: i + batch_size], return_tensors="pt")
            batch = batch.to(self._device)
            out = self._model(**batch)
            embs = out.embeddings.to(torch.float32).cpu().numpy()
            masks = batch["attention_mask"].bool().cpu().numpy()
            for e, m in zip(embs, masks):
                # 不裁切：padding 由 valid_mask 排除（與 encode_query 同形）
                results.append(EncodedVectors(embeddings=e, valid_mask=m))
        return results
```

- [ ] **Step 4.3: get_runtime 接上 lazy import（colpali_runtime.py 維持 torch-free）**

```python
def get_runtime(mock: bool = True, **kwargs):
    """mock=True → MockColPaliRuntime；mock=False → 真實 runtime（lazy import torch，D-L）。

    kwargs 透傳 RealColPaliRuntime（model_id / device / dtype）。
    """
    if mock:
        return MockColPaliRuntime()
    try:
        from anatomy_shared.colpali_real import RealColPaliRuntime
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "真實 ColPali runtime 需要 gpu 依賴：請以 colpali extra 安裝 "
            "（uv sync --package colpali-service --extra gpu）或使用 make up-gpu。"
        ) from e
    return RealColPaliRuntime(**kwargs)
```

- [ ] **Step 4.4: 驗證 torch-free 不破 + 單元全綠**

```bash
uv run --no-sync python -c "import anatomy_shared.colpali_runtime, sys; assert 'torch' not in sys.modules; print('OK')"
uv run --no-sync pytest shared/tests colpali_service/tests -q
```

Expected: `OK`；gpu/mt 測試顯示 skipped，其餘 PASS

- [ ] **Step 4.5: Commit**

```bash
git add shared/src/anatomy_shared/colpali_real.py shared/src/anatomy_shared/colpali_runtime.py shared/tests/test_loading_guard.py colpali_service/tests/test_real_runtime_gpu.py
git commit -m "feat(phase-3): 真實 ColPali runtime（bf16/SDPA、loading_info 精確守門、attention_mask 即 valid_mask）"
```

### Task 5: RealColPaliEncoder 組合 + get_encoder 真實路徑

**Files:**
- Create: `colpali_service/src/colpali_service/real_encoder.py`
- Modify: `colpali_service/src/colpali_service/encoder.py`
- Test: `colpali_service/tests/test_real_encoder_unit.py`

- [ ] **Step 5.1: 寫失敗測試（fake runtime=MockColPaliRuntime、fake translator；無 torch）**

```python
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
```

- [ ] **Step 5.2: 跑測試確認失敗**

```bash
uv run --no-sync pytest colpali_service/tests/test_real_encoder_unit.py -q
```

Expected: FAIL（`real_encoder` 不存在）

- [ ] **Step 5.3: 實作 real_encoder.py**

```python
"""§4.2 /encode_query 契約的真實實作：MT（DL-020）→ ColPali 編碼 → shared 二值化/池化。

組合物件本身無 torch import；重依賴在 build_real_encoder() 內經 get_runtime(mock=False)
/ build_marian_translator() lazy 取得（單元測試以 mock runtime + fake translator 注入）。
"""
import os

from anatomy_shared.binary import binarize, pool_patches

from colpali_service.translate import DEFAULT_MT_MODEL, LocalTranslator


class RealColPaliEncoder:
    """與 MockEncoder 同契約：encode_query(q) -> dict（main.py 負責 base64）。"""

    def __init__(self, runtime, translator: LocalTranslator):
        self._runtime = runtime
        self._translator = translator
        self.model = runtime.model_id
        self.mt_model = translator.mt_model_name

    def encode_query(self, q: str) -> dict:
        tr = self._translator.translate(q)
        # DL-020：zh 且 MT 成功 → 以英文編碼；MT 失敗 → 原文編碼；en → 原文
        text_for_model = tr.translated_q if (tr.lang == "zh" and tr.translated_q) else q
        enc = self._runtime.encode_query(text_for_model)
        valid = enc.embeddings[enc.valid_mask]
        pooled_f32 = pool_patches(enc.embeddings, valid_mask=enc.valid_mask).astype("<f4")
        return {
            "tokens_bin": [binarize(t) for t in valid],
            "pooled_f32": pooled_f32.tobytes(),
            "translated_q": tr.translated_q,
            "lang": tr.lang,
            "model": self.model,
            "mt_model": tr.mt_model,
        }


def build_real_encoder() -> RealColPaliEncoder:
    """從環境組真實 encoder（GPU 容器路徑）。"""
    from anatomy_shared.colpali_runtime import get_runtime

    from colpali_service.translate import build_marian_translator

    runtime = get_runtime(
        mock=False,
        model_id=os.environ.get("COLPALI_MODEL", "vidore/colpali-v1.3-hf"),
        device=os.environ.get("COLPALI_DEVICE", "cuda"),
    )
    translator = build_marian_translator(os.environ.get("MT_MODEL", DEFAULT_MT_MODEL))
    return RealColPaliEncoder(runtime=runtime, translator=translator)
```

- [ ] **Step 5.4: 改寫 encoder.py（_detect_lang 移到 translate、真實路徑接上）**

```python
"""Encoder 抽象：mock（決定性，delegate 到 shared runtime）與真實路徑的工廠。"""
import os

from anatomy_shared.binary import binarize, pool_patches
from anatomy_shared.colpali_runtime import MockColPaliRuntime

from colpali_service.translate import detect_lang


class MockEncoder:
    """決定性 mock：滿足 /encode_query 契約，供下游（後端 client、檢索）演練。

    向量來源＝shared 的 MockColPaliRuntime；二值化/池化＝shared.binary（§2.4 單一來源）。
    """

    ready = True
    mt_model = "mock-identity"

    def __init__(self) -> None:
        self._runtime = MockColPaliRuntime()
        self.model = self._runtime.model_id

    def encode_query(self, q: str) -> dict:
        enc = self._runtime.encode_query(q)
        valid = enc.embeddings[enc.valid_mask]   # 排除 padding/特殊前綴 token（§2.4 / roadmap AC）
        # DL-019：pooled 不二值化、全程 fp32（halfvec 量化只發生在 DB 綁定層）
        pooled_f32 = pool_patches(enc.embeddings, valid_mask=enc.valid_mask).astype("<f4")
        return {
            "tokens_bin": [binarize(t) for t in valid],
            "pooled_f32": pooled_f32.tobytes(),
            # DL-020：mock 為決定性 identity 翻譯（真實本地 MT 見 translate.py）
            "translated_q": q,
            "lang": detect_lang(q),
            "model": self.model,
            "mt_model": self.mt_model,
        }


def get_encoder():
    if os.environ.get("ENCODER_MOCK", "true").lower() == "true":
        return MockEncoder()
    try:
        from colpali_service.real_encoder import build_real_encoder
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "真實 encoder 需要 gpu 依賴（torch/transformers/sentencepiece/OpenCC）："
            "請以 gpu extra 安裝（uv sync --package colpali-service --extra gpu）"
            "並用 make up-gpu 啟動；mock 請設 ENCODER_MOCK=true。"
        ) from e
    return build_real_encoder()
```

註：`build_real_encoder` import 成功但其內部 lazy import 缺 torch 時，`get_runtime` 會拋
帶安裝指引的 RuntimeError（Task 4），訊息同樣清楚。

- [ ] **Step 5.4b: 刪除被取代的舊測試（同一 commit 內保持綠；Codex 審查 #10）**

刪除 `colpali_service/tests/test_encode.py` 的 `test_get_encoder_real_not_implemented_yet`
（其守護目標「ENCODER_MOCK=false 給清楚錯誤」由 Step 5.1 的
`test_get_encoder_real_without_gpu_extra_raises_clear_error` 接手）。

- [ ] **Step 5.5: 跑測試**

```bash
uv run --no-sync pytest colpali_service/tests -q
```

Expected: 全部 PASS（綠 commit；gpu/mt skip）

- [ ] **Step 5.6: Commit**

```bash
git add colpali_service/src/colpali_service/real_encoder.py colpali_service/src/colpali_service/encoder.py colpali_service/tests/test_real_encoder_unit.py colpali_service/tests/test_encode.py
git commit -m "feat(phase-3): RealColPaliEncoder（MT→編碼→shared 二值化/池化）+ get_encoder 真實路徑"
```

### Task 6: 服務 readiness（503 → ready）與非阻塞推理

**Files:**
- Modify: `colpali_service/src/colpali_service/main.py`（全檔改寫）
- Modify: `colpali_service/tests/test_encode.py`
- Test: `colpali_service/tests/test_service_readiness.py`

- [ ] **Step 6.1: 寫 readiness 失敗測試**

```python
"""readiness 行為（§5.1 MUST：模型載入完成才 healthy）：載入前 /healthz、/encode_query 皆 503。"""
import asyncio
import threading

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_not_ready_returns_503_then_ready(monkeypatch):
    import colpali_service.main as m

    gate = threading.Event()

    class SlowEncoder:
        model = "slow-fake"
        mt_model = "fake-mt"

        def encode_query(self, q):
            from colpali_service.encoder import MockEncoder
            return MockEncoder().encode_query(q)

    def slow_get_encoder():
        gate.wait(timeout=10)
        return SlowEncoder()

    monkeypatch.setenv("ENCODER_MOCK", "false")          # 走背景執行緒載入路徑
    monkeypatch.setattr(m, "get_encoder", slow_get_encoder)
    async with LifespanManager(m.app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            r = await c.get("/healthz")
            assert r.status_code == 503 and r.json()["ready"] is False
            r = await c.post("/encode_query", json={"q": "x"})
            assert r.status_code == 503
            gate.set()                                   # 放行載入
            for _ in range(100):
                r = await c.get("/healthz")
                if r.status_code == 200:
                    break
                await asyncio.sleep(0.05)
            assert r.status_code == 200 and r.json()["ready"] is True
            r = await c.post("/encode_query", json={"q": "肱二頭肌"})
            assert r.status_code == 200 and r.json()["lang"] == "zh"


@pytest.mark.asyncio
async def test_load_failure_stays_503_with_error(monkeypatch):
    import colpali_service.main as m

    def broken():
        raise RuntimeError("weights corrupted")

    monkeypatch.setenv("ENCODER_MOCK", "false")
    monkeypatch.setattr(m, "get_encoder", broken)
    async with LifespanManager(m.app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            for _ in range(100):
                r = await c.get("/healthz")
                if r.json().get("error"):
                    break
                await asyncio.sleep(0.05)
            assert r.status_code == 503
            assert "weights corrupted" in r.json()["error"]


@pytest.mark.asyncio
async def test_stale_loader_cannot_pollute_new_lifespan(monkeypatch):
    """舊 lifespan 的慢載入完成後，不得覆寫新 lifespan 的狀態（Codex 審查 #8）。"""
    import colpali_service.main as m

    gate = threading.Event()
    calls = {"n": 0}

    class Enc:
        mt_model = "fake-mt"

        def __init__(self, name):
            self.model = name

        def encode_query(self, q):
            from colpali_service.encoder import MockEncoder
            return MockEncoder().encode_query(q)

    def loader():
        calls["n"] += 1
        if calls["n"] == 1:
            gate.wait(timeout=10)        # 第一個 lifespan 的載入卡住
            return Enc("stale")
        return Enc("fresh")

    monkeypatch.setenv("ENCODER_MOCK", "false")
    monkeypatch.setattr(m, "get_encoder", loader)
    async with LifespanManager(m.app):
        pass                              # 舊 lifespan 結束時 loader 仍卡在 gate
    async with LifespanManager(m.app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            for _ in range(100):
                r = await c.get("/healthz")
                if r.status_code == 200:
                    break
                await asyncio.sleep(0.05)
            assert r.json()["model"] == "fresh"
            gate.set()                    # 放行舊執行緒（寫進它自己那份舊 dict）
            await asyncio.sleep(0.2)
            r = await c.get("/healthz")
            assert r.json()["model"] == "fresh"   # 新狀態不被 stale 覆寫
```

- [ ] **Step 6.2: 跑測試確認失敗**

```bash
uv run --no-sync pytest colpali_service/tests/test_service_readiness.py -q
```

Expected: FAIL（現行 main.py 無 lifespan / 不回 503）

- [ ] **Step 6.3: 改寫 main.py**

```python
"""ColPali query encoder 微服務（§5.1）。

readiness：mock 同步載入（快、測試決定性）；真實模型走 lifespan 背景執行緒
（下載/載入耗時，不擋 event loop），就緒前 /healthz、/encode_query 回 503
（healthcheck 的 curl -f 因此正確視為 not-ready）。
推理經 anyio.to_thread + lock：GPU 推理序列化、event loop 不被阻塞。
"""
import base64
import logging
import os
import threading
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from colpali_service.encoder import get_encoder

logger = logging.getLogger(__name__)

_infer_lock = threading.Lock()   # GPU 推理序列化（跨 lifespan 共用無妨）


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 狀態為「每個 lifespan 一份」並掛在 app.state：舊 lifespan 的慢載入執行緒只持有
    # 它自己那份 dict 的參照，寫不進新 lifespan 的狀態（Codex 審查 #8 的 race 結構性消除）。
    state: dict = {"encoder": None, "error": None}
    app.state.enc = state

    def load() -> None:
        try:
            enc = get_encoder()
            enc.encode_query("肱二頭肌的起止點")   # §5.1 SHOULD：startup dummy encode（暖 MT+ColPali）
            state["encoder"] = enc                # ready ⇒ 已預熱（dummy encode 成功才掛上）
            logger.info("encoder 就緒：%s", enc.model)
        except Exception as e:  # noqa: BLE001 - 載入失敗必須呈現在 /healthz，不可讓執行緒靜默死亡
            state["error"] = repr(e)
            logger.exception("encoder 載入失敗")

    if os.environ.get("ENCODER_MOCK", "true").lower() == "true":
        load()                                    # mock：同步、即時 ready（測試決定性）
    else:
        threading.Thread(target=load, daemon=True).start()
    yield


app = FastAPI(title="colpali-encoder", version="0.0.0", lifespan=lifespan)


class EncodeRequest(BaseModel):
    q: str


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


@app.get("/healthz")
async def healthz(request: Request):
    """readiness：模型載入完成才 200（§5.1）；載入中/失敗回 503（curl -f 視為 unhealthy）。"""
    state = request.app.state.enc
    enc = state["encoder"]
    if enc is None:
        return JSONResponse(status_code=503,
                            content={"ready": False, "error": state["error"]})
    return {"ready": True, "model": enc.model, "mt_model": enc.mt_model}


def _encode_sync(enc, q: str) -> dict:
    with _infer_lock:                                     # GPU 推理序列化
        return enc.encode_query(q)


@app.post("/encode_query")
async def encode_query(request: Request, req: EncodeRequest) -> dict:
    enc = request.app.state.enc["encoder"]
    if enc is None:
        raise HTTPException(status_code=503, detail="encoder 尚未就緒（模型載入中或失敗，見 /healthz）")
    out = await anyio.to_thread.run_sync(_encode_sync, enc, req.q)
    return {
        "tokens_bin": [_b64(t) for t in out["tokens_bin"]],
        "pooled_f32": _b64(out["pooled_f32"]),     # DL-019：512B LE float32[128]
        "translated_q": out["translated_q"],        # DL-020：BM25 用；MT 失敗為 null
        "lang": out["lang"],
        "model": out["model"],
        "mt_model": out["mt_model"],
    }


@app.post("/warmup")
async def warmup(request: Request) -> dict:
    """全鏈路預熱（§5.1 SHOULD）：固定 zh 字串同時暖 MT 與 ColPali。"""
    enc = request.app.state.enc["encoder"]
    if enc is None:
        raise HTTPException(status_code=503, detail="encoder 尚未就緒")
    await anyio.to_thread.run_sync(_encode_sync, enc, "肱二頭肌的起止點")
    return {"warmed": True}
```

- [ ] **Step 6.4: 更新 test_encode.py（LifespanManager + 刪除被取代的測試）**

改動點：(1) 全部 client 建立改走 helper（lifespan 啟動，mock 同步就緒）；(2) 其餘斷言不變
（`test_get_encoder_real_not_implemented_yet` 已於 Task 5.4b 刪除）。檔首加：

```python
from contextlib import asynccontextmanager

from asgi_lifespan import LifespanManager


@asynccontextmanager
async def _client():
    from colpali_service.main import app

    async with LifespanManager(app) as mgr:
        async with AsyncClient(transport=ASGITransport(app=mgr.app), base_url="http://t") as c:
            yield c
```

各測試將 `async with AsyncClient(...) as c:` 替換為 `async with _client() as c:`。

- [ ] **Step 6.5: 全測試 + lint**

```bash
uv run --no-sync pytest colpali_service/tests shared/tests -q && uv run --no-sync ruff check colpali_service shared
```

Expected: PASS（gpu/mt skip）、ruff 乾淨

- [ ] **Step 6.6: Commit**

```bash
git add colpali_service/src/colpali_service/main.py colpali_service/tests/test_encode.py colpali_service/tests/test_service_readiness.py
git commit -m "feat(phase-3): readiness 503→ready（lifespan 背景載入）+ to_thread/lock 非阻塞推理"
```

### Task 7: Docker / compose / Makefile

**Files:**
- Modify: `colpali_service/Dockerfile`
- Modify: `docker-compose.gpu.yml`
- Modify: `Makefile`

- [ ] **Step 7.1: GPU Dockerfile 更新**

改動（其餘維持原樣）：

```dockerfile
ENV PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive ENCODER_MOCK=false COLPALI_DEVICE=cuda \
    UV_NO_SYNC=1 UV_HTTP_TIMEOUT=600 HF_HOME=/hf-cache
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.11 python3.11-venv python3-pip git curl fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
```

```dockerfile
# gpu extra（torch/transformers/sentencepiece/OpenCC）+ eval（recall gate 的 harness）；torch 換 cu128 最後做。
# torch pin 2.11.*（Phase 0 gpu-smoke 驗證過的版本；Codex 審查 #11——不追最新 cu128）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package colpali-service --extra gpu --no-dev && \
    uv sync --package anatomy-eval --inexact --no-dev && \
    uv pip install "torch==2.11.*" --index-url https://download.pytorch.org/whl/cu128 --reinstall
```

> 已知優化空間（**不在本 phase**，列 backlog）：manifests-first Docker 分層（先 COPY 各成員
> pyproject + uv.lock 裝依賴、再 COPY 原始碼）可避免改原始碼就重灌依賴層——此 pattern 自
> Phase 0 起所有 Dockerfile 一致，應整批處理；現有 uv cache mount 已避免重抓 GB 級 wheel
> （Codex 審查 #12，裁決＝延後）。

```dockerfile
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=60 \
  CMD curl -f http://localhost:8001/healthz || exit 1
```

- [ ] **Step 7.2: docker-compose.gpu.yml 加 HF 快取 volume + healthcheck 放寬**

```yaml
# docker compose -f docker-compose.yml -f docker-compose.gpu.yml up
services:
  encoder:
    build: { context: ., dockerfile: colpali_service/Dockerfile }   # GPU 版
    environment: { ENCODER_MOCK: "false", COLPALI_DEVICE: cuda, HF_HOME: /hf-cache }
    volumes: [ "hfcache:/hf-cache" ]          # 模型權重快取：重建 image / 重啟不重抓 ~7GB
    healthcheck:                               # 覆蓋核心 compose 的 5s×10：真模型載入需分鐘級
      test: ["CMD", "curl", "-f", "http://localhost:8001/healthz"]
      interval: 10s
      timeout: 5s
      retries: 60
      start_period: 60s
  backend:
    environment: { ENCODER_MOCK: "false" }
volumes: { hfcache: {} }
```

- [ ] **Step 7.3: Makefile 加 encoder-models / encoder-gate**

```make
# 預拉 HF 模型進 hfcache volume（首次 ~7–8GB；之後重建/重啟免重抓）。需先 build GPU image。
encoder-models:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml build encoder
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps encoder \
	  uv run --no-sync python -c "from huggingface_hub import snapshot_download as d; d('vidore/colpali-v1.3-hf'); d('Helsinki-NLP/opus-mt-zh-en'); print('models cached')"

# Phase 3 recall gate（D-P；手動 GPU，非 CI）：渲染偽頁面→真模型編碼→zh/en recall@3
encoder-gate:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps encoder \
	  uv run --no-sync python colpali_service/scripts/encoder_gate.py
```

並在 `help` 加兩行說明、`.PHONY` 追加 `encoder-models encoder-gate`。

- [ ] **Step 7.4: 驗 compose 設定**

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config -q && echo OK
```

Expected: `OK`

- [ ] **Step 7.5: Commit**

```bash
git add colpali_service/Dockerfile docker-compose.gpu.yml Makefile
git commit -m "build(phase-3): HF 模型快取 volume、encoder-models/encoder-gate targets、GPU healthcheck 放寬"
```

### Task 8: recall gate（D-P）——fixtures + 腳本

**Files:**
- Create: `eval/data/encoder_gate_pages.jsonl`
- Create: `eval/data/encoder_gate_queries.jsonl`
- Create: `colpali_service/scripts/encoder_gate.py`

- [ ] **Step 8.1: 偽頁面 fixtures（16 頁=12 出題主題 + 4 近鄰干擾頁；自撰英文解剖短文）**

`eval/data/encoder_gate_pages.jsonl`，每行 `{"page_id": ..., "title": ..., "text": ...}`。
每篇為 60–90 詞的教科書式原創描述（禁止抄錄受版權教材），**必須包含下表「必含關鍵詞」**
（防 author-tuning：主題與關鍵詞已定案，implementer 只負責把關鍵詞組成自然散文）：

| page_id | title | 必含關鍵詞 |
|---|---|---|
| `gate:biceps` | Biceps Brachii | supraglenoid tubercle, coracoid process, radial tuberosity, musculocutaneous nerve |
| `gate:triceps`（干擾） | Triceps Brachii | infraglenoid tubercle, olecranon, radial nerve |
| `gate:median-nerve` | Median Nerve in the Forearm | flexor digitorum superficialis, pronator teres, anterior interosseous, carpal tunnel |
| `gate:ulnar-nerve`（干擾） | Ulnar Nerve | medial epicondyle, flexor carpi ulnaris, Guyon canal |
| `gate:femoral-head` | Blood Supply of the Femoral Head | medial circumflex femoral artery, retinacular arteries, ligamentum teres |
| `gate:hip-capsule`（干擾） | Hip Joint Capsule and Ligaments | iliofemoral ligament, acetabular labrum |
| `gate:brachial-plexus` | Brachial Plexus | roots, trunks, divisions, cords, terminal branches |
| `gate:mediastinum-sup` | Superior Mediastinum | aortic arch, brachiocephalic veins, trachea, thymus |
| `gate:mediastinum-inf`（干擾） | Inferior Mediastinum | pericardium, esophagus, descending thoracic aorta |
| `gate:heart-valves` | Valves of the Heart | mitral valve, tricuspid valve, aortic valve, pulmonary valve |
| `gate:liver` | Lobes of the Liver | right lobe, left lobe, caudate lobe, quadrate lobe, falciform ligament |
| `gate:nephron` | The Nephron | glomerulus, Bowman capsule, loop of Henle, filtration |
| `gate:bronchi` | Bronchial Tree | main bronchus, lobar bronchi, segmental bronchi, carina |
| `gate:skull-foramina` | Foramina of the Skull Base | foramen magnum, jugular foramen, foramen ovale, optic canal |
| `gate:knee-ligaments` | Ligaments of the Knee | anterior cruciate ligament, posterior cruciate ligament, medial collateral ligament, menisci |
| `gate:inguinal-canal` | The Inguinal Canal | deep inguinal ring, superficial inguinal ring, spermatic cord, external oblique aponeurosis |

格式範例（第 1 行）：

```jsonl
{"page_id": "gate:biceps", "title": "Biceps Brachii", "text": "The biceps brachii is a two-headed muscle of the anterior compartment of the arm. Its long head originates from the supraglenoid tubercle of the scapula, and the short head from the coracoid process. Both heads insert onto the radial tuberosity and the bicipital aponeurosis. The muscle flexes the elbow and supinates the forearm, and is innervated by the musculocutaneous nerve (C5, C6)."}
```

- [ ] **Step 8.2: 查詢 fixtures（24 題全文定案；GoldenQA schema、category 一律 `text_only`）**

`eval/data/encoder_gate_queries.jsonl` **完整內容如下**（zh 配比：6 glossary 題 001–006、
4 非 glossary 題 007–010、2 混語題 011–012；4 個干擾頁不出題）：

```jsonl
{"id": "gate-zh-001", "category": "text_only", "query": "肱二頭肌的起止點是什麼？", "expected_pages": ["gate:biceps"]}
{"id": "gate-zh-002", "category": "text_only", "query": "正中神經在前臂支配哪些肌肉？", "expected_pages": ["gate:median-nerve"]}
{"id": "gate-zh-003", "category": "text_only", "query": "股骨頭的血液供應來自哪些動脈？", "expected_pages": ["gate:femoral-head"]}
{"id": "gate-zh-004", "category": "text_only", "query": "臂神經叢的根、幹、股、束如何排列？", "expected_pages": ["gate:brachial-plexus"]}
{"id": "gate-zh-005", "category": "text_only", "query": "上縱隔內有哪些重要構造？", "expected_pages": ["gate:mediastinum-sup"]}
{"id": "gate-zh-006", "category": "text_only", "query": "膝關節由哪些主要韌帶維持穩定？", "expected_pages": ["gate:knee-ligaments"]}
{"id": "gate-zh-007", "category": "text_only", "query": "心臟的四個瓣膜分別位在哪裡？", "expected_pages": ["gate:heart-valves"]}
{"id": "gate-zh-008", "category": "text_only", "query": "肝在解剖上分為哪幾葉？", "expected_pages": ["gate:liver"]}
{"id": "gate-zh-009", "category": "text_only", "query": "腎元是什麼？它如何過濾血液？", "expected_pages": ["gate:nephron"]}
{"id": "gate-zh-010", "category": "text_only", "query": "顱底有哪些孔洞讓腦神經通過？", "expected_pages": ["gate:skull-foramina"]}
{"id": "gate-zh-011", "category": "text_only", "query": "biceps brachii 的神經支配是什麼？", "expected_pages": ["gate:biceps"]}
{"id": "gate-zh-012", "category": "text_only", "query": "inguinal canal 的邊界與內容物有哪些？", "expected_pages": ["gate:inguinal-canal"]}
{"id": "gate-en-001", "category": "text_only", "query": "What are the origin and insertion of the biceps brachii?", "expected_pages": ["gate:biceps"]}
{"id": "gate-en-002", "category": "text_only", "query": "Which forearm muscles are innervated by the median nerve?", "expected_pages": ["gate:median-nerve"]}
{"id": "gate-en-003", "category": "text_only", "query": "Which arteries supply blood to the femoral head?", "expected_pages": ["gate:femoral-head"]}
{"id": "gate-en-004", "category": "text_only", "query": "Describe the roots, trunks, divisions and cords of the brachial plexus.", "expected_pages": ["gate:brachial-plexus"]}
{"id": "gate-en-005", "category": "text_only", "query": "What structures lie within the superior mediastinum?", "expected_pages": ["gate:mediastinum-sup"]}
{"id": "gate-en-006", "category": "text_only", "query": "Where are the four valves of the heart located?", "expected_pages": ["gate:heart-valves"]}
{"id": "gate-en-007", "category": "text_only", "query": "How is the liver divided into lobes?", "expected_pages": ["gate:liver"]}
{"id": "gate-en-008", "category": "text_only", "query": "What is the functional filtration unit of the kidney?", "expected_pages": ["gate:nephron"]}
{"id": "gate-en-009", "category": "text_only", "query": "How does the bronchial tree branch within the lungs?", "expected_pages": ["gate:bronchi"]}
{"id": "gate-en-010", "category": "text_only", "query": "Which skull base foramina transmit the cranial nerves?", "expected_pages": ["gate:skull-foramina"]}
{"id": "gate-en-011", "category": "text_only", "query": "Which ligaments stabilize the knee joint?", "expected_pages": ["gate:knee-ligaments"]}
{"id": "gate-en-012", "category": "text_only", "query": "What are the boundaries and contents of the inguinal canal?", "expected_pages": ["gate:inguinal-canal"]}
```

- [ ] **Step 8.3: encoder_gate.py**

```python
"""Phase 3 encoder smoke gate（D-P 種子；非 DL-013 上線 gate）——手動 GPU 腳本，非 CI。

流程：16 個英文偽頁面（PIL 渲染；含 4 近鄰干擾頁）→ 真實 ColPali encode_pages +
shared.binarize → 24 題 zh/en query 走 RealColPaliEncoder 完整管線（含 MT）→
雙軌排序皆 gate：(1) maxsim_hamming（Stage B 軌）(2) pooled cosine（Stage A 軌，DL-019）；
任一軌低於門檻 exit 1。並印每題 zh 的 translated_q（人工抽查 MT）與 encode/MT 延遲。

執行：make encoder-gate（GPU 容器內；需先 make encoder-models）
"""
import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]   # /app（容器）或 repo 根
PAGES = REPO / "eval/data/encoder_gate_pages.jsonl"
QUERIES = REPO / "eval/data/encoder_gate_queries.jsonl"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def render_page(title: str, text: str):
    """白底黑字偽頁面（896×1152）；ColPali processor 會自行 resize。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (896, 1152), "white")
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(FONT, 40)
    body_font = ImageFont.truetype(FONT, 28)
    draw.text((48, 48), title, fill="black", font=title_font)
    y = 140
    for line in textwrap.wrap(text, width=52):
        draw.text((48, y), line, fill="black", font=body_font)
        y += 40
    return img


# —— fixture 契約（計畫 Task 8.1/8.2 的單一執行來源；偏離即 fail-closed，防 author-tuning）——
EXPECTED_PAGE_IDS = frozenset({
    "gate:biceps", "gate:triceps", "gate:median-nerve", "gate:ulnar-nerve",
    "gate:femoral-head", "gate:hip-capsule", "gate:brachial-plexus", "gate:mediastinum-sup",
    "gate:mediastinum-inf", "gate:heart-valves", "gate:liver", "gate:nephron",
    "gate:bronchi", "gate:skull-foramina", "gate:knee-ligaments", "gate:inguinal-canal",
})
REQUIRED_KEYWORDS = {
    "gate:biceps": ["supraglenoid tubercle", "coracoid process", "radial tuberosity",
                    "musculocutaneous nerve"],
    "gate:triceps": ["infraglenoid tubercle", "olecranon", "radial nerve"],
    "gate:median-nerve": ["flexor digitorum superficialis", "pronator teres",
                          "anterior interosseous", "carpal tunnel"],
    "gate:ulnar-nerve": ["medial epicondyle", "flexor carpi ulnaris", "Guyon"],
    "gate:femoral-head": ["medial circumflex femoral artery", "retinacular", "ligamentum teres"],
    "gate:hip-capsule": ["iliofemoral ligament", "acetabular labrum"],
    "gate:brachial-plexus": ["roots", "trunks", "divisions", "cords", "terminal branches"],
    "gate:mediastinum-sup": ["aortic arch", "brachiocephalic veins", "trachea", "thymus"],
    "gate:mediastinum-inf": ["pericardium", "esophagus", "descending thoracic aorta"],
    "gate:heart-valves": ["mitral valve", "tricuspid valve", "aortic valve", "pulmonary valve"],
    "gate:liver": ["right lobe", "left lobe", "caudate lobe", "quadrate lobe",
                   "falciform ligament"],
    "gate:nephron": ["glomerulus", "Bowman capsule", "loop of Henle", "filtration"],
    "gate:bronchi": ["main bronchus", "lobar bronchi", "segmental bronchi", "carina"],
    "gate:skull-foramina": ["foramen magnum", "jugular foramen", "foramen ovale", "optic canal"],
    "gate:knee-ligaments": ["anterior cruciate ligament", "posterior cruciate ligament",
                            "medial collateral ligament", "menisci"],
    "gate:inguinal-canal": ["deep inguinal ring", "superficial inguinal ring", "spermatic cord",
                            "external oblique aponeurosis"],
}
EXPECTED_QUERY_PAGES = {
    "gate-zh-001": "gate:biceps", "gate-zh-002": "gate:median-nerve",
    "gate-zh-003": "gate:femoral-head", "gate-zh-004": "gate:brachial-plexus",
    "gate-zh-005": "gate:mediastinum-sup", "gate-zh-006": "gate:knee-ligaments",
    "gate-zh-007": "gate:heart-valves", "gate-zh-008": "gate:liver",
    "gate-zh-009": "gate:nephron", "gate-zh-010": "gate:skull-foramina",
    "gate-zh-011": "gate:biceps", "gate-zh-012": "gate:inguinal-canal",
    "gate-en-001": "gate:biceps", "gate-en-002": "gate:median-nerve",
    "gate-en-003": "gate:femoral-head", "gate-en-004": "gate:brachial-plexus",
    "gate-en-005": "gate:mediastinum-sup", "gate-en-006": "gate:heart-valves",
    "gate-en-007": "gate:liver", "gate-en-008": "gate:nephron",
    "gate-en-009": "gate:bronchi", "gate-en-010": "gate:skull-foramina",
    "gate-en-011": "gate:knee-ligaments", "gate-en-012": "gate:inguinal-canal",
}


def check_fixtures(pages: list[dict], golden, detect_lang) -> None:
    """fixture 契約執法，fail-closed（Codex 審查 #13 + 複審 #3）。"""
    by_id = {p["page_id"]: p for p in pages}
    if len(pages) != 16 or set(by_id) != EXPECTED_PAGE_IDS:
        raise SystemExit(
            f"fixture 錯誤：page_id 集合與計畫不符 {sorted(set(by_id) ^ EXPECTED_PAGE_IDS)}")
    for pid, kws in REQUIRED_KEYWORDS.items():
        miss = [k for k in kws if k.lower() not in by_id[pid]["text"].lower()]
        if miss:
            raise SystemExit(f"fixture 錯誤：{pid} 缺必含關鍵詞 {miss}")
    got = {qa.id: tuple(qa.expected_pages) for qa in golden}
    want = {k: (v,) for k, v in EXPECTED_QUERY_PAGES.items()}
    if got != want:
        bad = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
        raise SystemExit(f"fixture 錯誤：查詢 id/expected_pages 與計畫不符 {bad}")
    zh = [qa for qa in golden if detect_lang(qa.query) == "zh"]
    if len(zh) != 12:
        raise SystemExit(f"fixture 錯誤：zh 題數 {len(zh)} != 12")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--min-maxsim-en", type=float, default=0.9)
    ap.add_argument("--min-maxsim-zh", type=float, default=0.75)
    ap.add_argument("--min-pooled-en", type=float, default=0.75)
    ap.add_argument("--min-pooled-zh", type=float, default=0.6)
    args = ap.parse_args()

    from anatomy_eval.golden import load_golden
    from anatomy_eval.harness import evaluate_recall_by_class
    from anatomy_eval.reference import maxsim_hamming
    from anatomy_shared.binary import binarize, pool_patches

    from colpali_service.real_encoder import build_real_encoder
    from colpali_service.translate import detect_lang

    pages = [json.loads(line) for line in PAGES.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden = load_golden(QUERIES)
    check_fixtures(pages, golden, detect_lang)

    print("== 載入真實 encoder（含 loading_info 守門）與 MT ==")
    t0 = time.perf_counter()
    encoder = build_real_encoder()
    print(f"載入完成：{time.perf_counter() - t0:.1f}s")

    print(f"== 編碼 {len(pages)} 個偽頁面 ==")
    runtime = encoder._runtime
    encs = runtime.encode_pages([render_page(p["title"], p["text"]) for p in pages])
    if len(encs) != len(pages):                      # 截斷的批次編碼不得放行（Codex 複審 #2）
        raise SystemExit(f"encode_pages 回傳 {len(encs)} != 頁數 {len(pages)}")
    page_bins = {}
    page_pooled = {}
    for p, enc in zip(pages, encs):
        valid = enc.embeddings[enc.valid_mask]
        page_bins[p["page_id"]] = [binarize(v) for v in valid]
        pooled = pool_patches(enc.embeddings, valid_mask=enc.valid_mask)
        if not (np.isfinite(pooled).all() and np.linalg.norm(pooled) > 0):   # DL-019 健檢
            raise SystemExit(f"頁面 pooled 無效（NaN/零向量）：{p['page_id']}")
        page_pooled[p["page_id"]] = pooled

    # 先一次編碼全部 query（並量延遲），雙軌排序共用同一份編碼結果
    lat: list[float] = []
    enc_cache: dict[str, dict] = {}
    for qa in golden:
        t = time.perf_counter()
        enc_cache[qa.id] = encoder.encode_query(qa.query)
        lat.append(time.perf_counter() - t)
        if detect_lang(qa.query) == "zh":
            print(f"  [{qa.id}] {qa.query} -> translated_q={enc_cache[qa.id]['translated_q']!r}")

    def retrieve_maxsim(qa) -> list[str]:
        out = enc_cache[qa.id]
        return sorted(page_bins, key=lambda pid: -maxsim_hamming(out["tokens_bin"], page_bins[pid]))

    def retrieve_pooled(qa) -> list[str]:
        q = np.frombuffer(enc_cache[qa.id]["pooled_f32"], dtype="<f4")
        if not (np.isfinite(q).all() and np.linalg.norm(q) > 0):             # DL-019 健檢
            raise SystemExit(f"query pooled 無效（NaN/零向量）：{qa.id}")
        return sorted(page_pooled, key=lambda pid: -float(
            np.dot(q, page_pooled[pid]) / (np.linalg.norm(q) * np.linalg.norm(page_pooled[pid]))))

    zh = [qa for qa in golden if detect_lang(qa.query) == "zh"]
    en = [qa for qa in golden if detect_lang(qa.query) == "en"]
    reports = {
        ("maxsim", "zh"): evaluate_recall_by_class(zh, retrieve_maxsim, k=args.k),
        ("maxsim", "en"): evaluate_recall_by_class(en, retrieve_maxsim, k=args.k),
        ("pooled", "zh"): evaluate_recall_by_class(zh, retrieve_pooled, k=args.k),
        ("pooled", "en"): evaluate_recall_by_class(en, retrieve_pooled, k=args.k),
    }
    thresholds = {
        ("maxsim", "zh"): args.min_maxsim_zh, ("maxsim", "en"): args.min_maxsim_en,
        ("pooled", "zh"): args.min_pooled_zh, ("pooled", "en"): args.min_pooled_en,
    }

    lat_ms = sorted(int(s * 1000) for s in lat)
    print(f"\nencode_query 延遲（含 MT）：p50={lat_ms[len(lat_ms)//2]}ms max={lat_ms[-1]}ms")
    ok = True
    for key, rep in reports.items():
        track, lang_name = key
        passed = rep["overall"] >= thresholds[key]
        ok = ok and passed
        print(f"{track} {lang_name} recall@{args.k}={rep['overall']:.3f}"
              f"（門檻 {thresholds[key]}）{'PASS' if passed else 'FAIL'}")

    print("GATE PASS" if ok else "GATE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

註：腳本直接組 encoder 物件（不走 HTTP）——gate 的對象是「MT→編碼→二值化」品質，
HTTP 契約由 Task 6 測試與 Task 9 的 curl 驗收覆蓋。`encoder._runtime` 取私有屬性
僅限本腳本（gate 需要 encode_pages；不值得為此擴公開契約）。

- [ ] **Step 8.4: fixtures 格式驗證（無 GPU 也可跑）**

```bash
uv sync --package anatomy-eval --inexact
uv run --no-sync python - <<'EOF'
import json
from pathlib import Path
from anatomy_eval.golden import load_golden
pages = {json.loads(l)["page_id"] for l in Path("eval/data/encoder_gate_pages.jsonl").read_text().splitlines() if l.strip()}
golden = load_golden("eval/data/encoder_gate_queries.jsonl")
assert len(pages) == 16, len(pages)
assert len(golden) == 24, len(golden)
missing = [qa.id for qa in golden for p in qa.expected_pages if p not in pages]
assert not missing, missing
zh = sum(1 for qa in golden if any('一' <= ch <= '鿿' for ch in qa.query))
assert zh == 12, zh
print("fixtures OK")
EOF
```

Expected: `fixtures OK`（同組不變量於 `encoder_gate.py` 內 `check_fixtures` fail-closed 重驗）

- [ ] **Step 8.5: Commit**

```bash
git add eval/data/encoder_gate_pages.jsonl eval/data/encoder_gate_queries.jsonl colpali_service/scripts/encoder_gate.py
git commit -m "feat(phase-3): D-P encoder recall gate（偽頁面渲染 + maxsim oracle + zh/en 門檻）"
```

### Task 9: 實機 GPU 驗收 + SETUP.md（在使用者機器執行；WSL2 + RTX 5060 Ti）

**Files:**
- Modify: `SETUP.md`（GPU encoder 啟用段）

- [ ] **Step 9.1: 預拉模型 + 起 GPU 服務**

```bash
make encoder-models        # 首次 ~7–8GB，視網速 10–60 分鐘
make up-gpu
docker compose ps encoder  # 等待 healthy（模型載入約 30–120s）
```

Expected: encoder 服務 `healthy`；期間 `curl -s localhost:8001/healthz` 先回 503 JSON、後回
`{"ready": true, "model": "vidore/colpali-v1.3-hf", "mt_model": "Helsinki-NLP/opus-mt-zh-en"}`

- [ ] **Step 9.2: 真實 /encode_query 契約驗收**

```bash
curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' \
  -d '{"q": "肱二頭肌的起止點"}' | python3 -c "
import json,sys,base64
j = json.load(sys.stdin)
assert len(base64.b64decode(j['pooled_f32'])) == 512
assert all(len(base64.b64decode(t)) == 16 for t in j['tokens_bin'])
assert j['lang'] == 'zh' and j['model'] == 'vidore/colpali-v1.3-hf'
print('tokens:', len(j['tokens_bin']), '| translated_q:', j['translated_q'])"
curl -s -X POST localhost:8001/warmup
```

Expected: translated_q 為含 `biceps brachii` 的英文句；warmup 回 `{"warmed": true}`

- [ ] **Step 9.3: gpu/mt 標記測試（容器內）+ recall gate**

```bash
# GPU image 為 --no-dev（無 pytest）→ 容器內先補 dev 群組再跑（Codex 審查 #3；--inexact 不剪已裝依賴）
docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps encoder \
  sh -c "uv sync --group dev --inexact && uv run --no-sync pytest colpali_service/tests/test_real_runtime_gpu.py -q"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps -e RUN_MT_TESTS=1 encoder \
  sh -c "uv sync --group dev --inexact && uv run --no-sync pytest colpali_service/tests/test_marian_mt.py -q"
make encoder-gate
```

Expected: 兩組測試 PASS；gate 印出每題 translated_q 與延遲，結尾 `GATE PASS`（exit 0）。
若 `GATE FAIL`：先人工看 translated_q 品質（MT 問題→補 glossary 詞條重跑；仍不過→停下，
依 DL-020 升級序回報使用者裁決）。**門檻不可為了過關而調低**（調整須附理由記入 PR）。

- [ ] **Step 9.4: mock 路徑回歸（核心 compose 不受影響）**

```bash
make up && sleep 10 && curl -sf localhost:8001/healthz && make test
```

Expected: mock encoder healthy（即時 ready）、全測試綠（gpu/mt skip）

- [ ] **Step 9.5: SETUP.md 追加「§ GPU encoder 啟用（Phase 3）」段**

內容須含：前置（gpu-smoke 已過）、`make encoder-models`（磁碟/時間預估、hfcache volume 說明）、
`make up-gpu` 與 healthy 等待行為（503→200 屬正常）、Step 9.2 的 curl 驗收（含預期輸出）、
`make encoder-gate` 與 gate 失敗時的處置（補 glossary / 回報）、常見排錯
（CUDA 不可用→gpu-smoke；下載逾時→重跑 encoder-models 續傳；VRAM 不足→關閉其他 GPU 程式）。

- [ ] **Step 9.6: 記錄實測數據 + Commit**

把 Step 9.3 的延遲數據（encode p50/max、模型載入秒數）記入 commit message 與 PR 說明
（Phase 5/8 延遲預算參考）。

```bash
git add SETUP.md
git commit -m "docs(phase-3): SETUP.md GPU encoder 啟用段 + 實機驗收數據"
```

### Task 10: 最終審查與收尾

- [ ] **Step 10.1: 全量驗證**

```bash
make lint && make test
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config -q
```

Expected: 全綠

- [ ] **Step 10.2: Codex 跨模型終審（full profile）**

對整個 change set 跑 `/codex:review`；critical/high 必須解決或upstream 回報使用者。
本 phase 觸及「Project-specific constraints」（encoder/二值化一致性、DL-020 DECIDED）→
屬 MUST review 項。

- [ ] **Step 10.3: 收尾**

依 superpowers:finishing-a-development-branch：使用者驗收後 ff-merge 進 main、推送、刪分支
（沿用 Phase 0–2 慣例）。

---

## 驗收標準對照（roadmap Phase 3 AC）

| Roadmap AC | 對應 |
|---|---|
| mock `/encode_query` 決定性、無 GPU 可跑（CI） | Task 6 test_encode.py（契約不變）+ CI unit job |
| 契約：token 數、pooled_f32 512B、translated_q/lang、base64 | Task 5/6 測試 + Task 9.2 真實 curl |
| 真實模式載入無 uninitialized 警告 | Task 4 `check_loading_info` 精確守門（fail-fast、已單元測試）+ Task 9.1 |
| binarize 與離線端一致 | by construction：兩端 import `shared.binary`（CI grep 既有）+ gate maxsim oracle |
| 本地 MT 對中文 query 產英文（DL-020） | Task 3 單元（含失敗/殘留守門）+ Task 9.3 mt 測試 + gate translated_q 抽查 |
| readiness 行為正確 | Task 6 測試（503→200、stale-loader 隔離）+ Task 9.1 實機 |
| 過 recall harness gate（D-P，含中文 query） | Task 8/9 smoke gate **雙軌**：MaxSim（zh≥0.75/en≥0.9）+ pooled cosine（zh≥0.6/en≥0.75）@3 |
| pooled_f32 品質可用（DL-019 Stage A） | gate pooled 軌門檻 + NaN/零向量 fail-closed |
| transformers 相容性驗證 | §0.2 研究 + Task 4 守門 + Task 9 實機（5.10.x；torch pin 2.11.* 斷言） |
