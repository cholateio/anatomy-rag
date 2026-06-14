# Phase 10 — 前端設計 Spec（anatomy-rag）

> 狀態：**待使用者複審** → 通過後進 `writing-plans` → Codex 對抗式審查（計畫）→ 實作。
> 日期：2026-06-14　作者：main（規劃）　Profile：full
> 權威來源：`docs/ARCHITECTURE.md` §5.6/§5.7/§5.8/§5.9/§6.7/§1.8、`docs/decisions.md`
> DL-012/016/018/021/022、roadmap §A（D-H/D-N/D-S）+ Phase 10 段。

## 0. 範圍與目標

Phase 10＝以 Next.js 16 App Router + Vercel AI SDK v6 `useChat` 打造解剖學 RAG 的**串流問答前端**：
先顯示引用面板 → 串流帶引文的答案 → 完成；首入強制免責同意；每答浮水印＋引文清單；未驗證引文警告 banner；
👍/👎＋文字回饋（**精準到單一回合**）；全繁體中文；mobile-first 單欄。

**不在範圍（沿用既有延後）**：真實 S3 頁圖（boto3 接線延後，dev 用佔位圖）；校內 SSO（DL-016，v1 免登入）；
會話歷史伺服器端持久化／reload（DL-021 無狀態後端；refresh 即清空，符合 v1）；語意向量快取（DL-025）。

## 1. 已確認決策（2026-06-14，使用者拍板）

| 項 | 決策 | 後果 |
|---|---|---|
| (a) 回饋粒度 | **per-turn** | 後端 pre-step：`turn_id` + `start.messageId` + migration 008 + `/feedback` 改 by `turn_id` |
| (a′) data part 持久性 | **persistent**（拿掉 `transient:true`） | 引文入 `message.parts`，前端 parts 渲染、SDK 自動綁回合；符合 D-H |
| (b) 套件 | **Tailwind v4 + shadcn(new-york)**；npm + `--legacy-peer-deps`；保留 `package-lock.json`（D-S） | 見 §7 |
| (c) dev 頁圖 | **佔位圖 on-error** | `<img onError>` fallback；真實圖等 S3 接線 |
| (d) Auth | **v1 免登入 UI** | dev stub 供 `user_id`；前端不送憑證 |
| (e) 協定/版本 | 鎖 `ai@6.0.197`/`@ai-sdk/react@3.0.199`；只用 `DefaultChatTransport`（**禁 text-stream**）；dump-golden 解耦 | 見 §6 |
| (f) decisions | 寫 **DL-027** | 見 §9 |

## 2. 架構與資料流

**傳輸**：`useChat`(`@ai-sdk/react`) + `new DefaultChatTransport({ api: '/chat' })`（從 `ai`）。**保證 UI-message-stream 模式**
（後端已帶 header `x-vercel-ai-ui-message-stream: v1`）；**禁用** `TextStreamChatTransport` / `streamProtocol:'text'`。

**型別**（`@/lib/types.ts`）：
```ts
import type { UIMessage } from 'ai';
export type Citation = {
  book_title: string; edition?: string | null; page: number;
  figure?: string | null; image_url: string; snippet: string; score: number;
};
export type SourcesData = { sources: Citation[] };
export type VerificationData = { verified: boolean; has_citations: boolean; unverified: string[] };
export type AnatomyUIMessage =
  UIMessage<never, { sources: SourcesData; verification: VerificationData }>;
// → part.type 'data-sources' → part.data: SourcesData；'data-verification' → VerificationData
```

**送出契約（DL-021）**：前端照常送整個 `messages`（useChat 預設）；後端只讀最後兩則 user 訊息→前端**零特例**。
`metadata_filter`/`conversation_id` 可透過 transport `body` 或 `sendMessage(_, {body})` 帶（v1 先不暴露 filter UI；
`conversation_id` 由前端生 uuid、整個分頁固定一個，供 query_logs 分組）。

**事件序（後端既有，persistent 後）**：`start(messageId=turn_id)` → `data-sources` → `text-start` → `text-delta*`
→ `text-end` → `data-verification` → `finish` → `[DONE]`。失敗路徑見 §5。

## 3. 元件分解（`@/components`，各自單一職責）

| 元件 | 職責 | 介面/輸入 | 依賴 |
|---|---|---|---|
| `ChatPanel` | useChat 容器、串接 transport、組裝畫面 | — | `@ai-sdk/react`, lib/transport |
| `MessageList` | 依 messages 渲染 | `messages: AnatomyUIMessage[]`, `status` | MessageBubble |
| `MessageBubble` | 單則訊息**依 part.type 抽取**渲染（見 §4） | `message`, `status` | Citation/Banner/Feedback/Watermark |
| `Composer` | 輸入框＋送出；`status!=='ready'` 禁用；**鍵盤友善**（釘底、auto-grow、font≥16px、`enterKeyHint=send`） | `onSend(text)`, `status` | shadcn Textarea/Button |
| `CitationPanel`+`CitationCard`+`CitationImage` | 讀 `data-sources`；圖佔位 fallback；**圖可點放大 lightbox** | `data: SourcesData` | 原生 `<img onError>`→佔位（不走 next/image，避免 remotePatterns 與 dev 404 摩擦）；shadcn Dialog（放大） |
| `UnverifiedBanner` | `verified===false` 顯示警告＋未驗證片段 | `data: VerificationData` | shadcn Alert |
| `FeedbackButtons` | 👍/👎＋文字；首次👎提示回報；送 `turn_id` | `messageId`, `onFeedback` | lib/api |
| `Watermark` | 每答底固定「教育用途，內容基於教科書」 | — | — |
| `DisclaimerModal` | 首入強制同意、`localStorage` 持久化；**手機呈 bottom-sheet／近全螢幕** | — | shadcn Dialog（responsive） |
| `EmptyState` | 無訊息時示例問題 | `onPick(q)` | — |
| `ErrorState` | `status==='error'` 友善訊息＋重試 | `error`, `onRetry` | — |

`@/lib`：`transport.ts`（DefaultChatTransport 工廠）、`types.ts`、`api.ts`（`postFeedback({messageId,rating,text})`）、
`disclaimer.ts`（localStorage 讀寫）、`utils.ts`（shadcn `cn()`）。

## 4. MessageBubble 渲染規則（關鍵）

assistant 訊息**不做線性 parts map，改依型別抽取以控制垂直序**，化解「§5.6 引用先顯示」×「§6.7 引文置底」：
1. **答案文字**：合併所有 `text` part（`part.text`）。串流中以 `status` 顯示打字游標。
2. **引文面板**（置文字下方）：取 `data-sources` part → `CitationPanel`。因 `data-sources` **先於**任何 text-delta 抵達，
   面板在文字串流前即掛載 → 滿足「先顯示引用面板」；位置在底部 → 滿足「§6.7 引文清單置底」。
3. **未驗證 banner**：取 `data-verification`；`verified===false && has_citations` → `UnverifiedBanner`（列出 `unverified` 片段）。
   `has_citations===false`（答案無引文，如「教材中查無此項」）→ 不顯示警告、僅正常呈現。
4. **底列**：`FeedbackButtons`(messageId=message.id) + `Watermark`。

> message.id 來源：後端 `start.messageId = turn_id`。**實作 pin-verify**：確認 `useChat` 以 `start.messageId` 設 `message.id`
> （beta→GA 穩定，但 6.0.197 需對 `node_modules/@ai-sdk/react` 型別核對）。**若否**→ fallback：把 `turn_id` 併入
> `data-verification` payload（`{...,turn_id}`），FeedbackButtons 改讀該欄。

## 5. 狀態與流程

**狀態映射（`status`: submitted|streaming|ready|error）**：
- `submitted`/`streaming` → 打字指示、輸入禁用、顯示 `stop`。
- `ready` → 可送出、可 `regenerate`。
- `error` → `ErrorState` ＋ 重試（regenerate）。
- 串流內後端 `{type:'error',errorText}`（encoder/retrieval/llm 失敗）→ useChat 進 error 狀態並帶 `errorText`。
  **pin-verify**：確認 UI-message-stream 的 error chunk 映射到 useChat `error`／中止串流。

**空狀態**：無 messages → `EmptyState` 示例問題（3 題解剖學）。
**追問**：純前端無特例（後端 DL-021 處理）；UX 上同一輸入框連續送出即可。

**免責同意流程（§6.7 MUST）**：
- App 首次掛載讀 `localStorage['anatomy-rag:disclaimer:v1']`；未同意 → `DisclaimerModal` 阻擋輸入。
- 內容三點：教育用途／系統可能出錯應自行驗證／查詢日誌會儲存供品質改善。
- 「我了解並同意」→ 寫 localStorage、關閉。版本化 key 便於改版重新同意。

**回饋流程（§6.7 + §6.5 + DL-022）**：
- 每則 assistant 答案底：👍/👎。點擊 → `POST /feedback {message_id: turn_id, rating: 1|-1, text?}`。
- 👎 → 展開選填文字框（送出帶 text）。
- **首次👎**（`localStorage['anatomy-rag:first-downvote']` 未設）→ 顯示回報機制說明（助教每週檢視倒讚案例），之後不再提示。

## 5.1 RWD／Mobile-first（主要使用情境，重點）

> 假設：**多數使用者用手機提問**。版面、互動、效能以手機為第一目標，桌面為漸進增強——**同一單欄、置中加寬，無分叉 codepath**。

**App shell（全高、防鍵盤遮擋）**
- 三段式：compact header（標題＋教育用途 badge）／可捲動 message list（`flex-1` `overflow-y-auto`）／composer 釘底。
- 高度用 **`100dvh`/`100svh`（dynamic/small viewport）**、**不用 `100vh`**——避免手機網址列收合與軟鍵盤改變高度造成跳動或遮擋。
- **safe-area**：`viewport-fit=cover` + composer 底 padding 加 `env(safe-area-inset-bottom)`、header 加 inset-top（瀏海／home indicator）。
- `app/layout.tsx` 補 `export const viewport = { width:'device-width', initialScale:1, viewportFit:'cover' }`（目前缺）。

**鍵盤與輸入（手機聊天最大痛點）**
- Composer 釘底；新訊息自動捲到底；軟鍵盤開啟時 composer 仍可見（dvh + sticky；必要時 `visualViewport` 微調）。
- **textarea `font-size ≥ 16px`**（iOS <16px 會自動放大頁面）；auto-grow（1→N 行、達上限內捲）；`enterKeyHint="send"`。

**觸控目標**：👍/👎、送出、引文卡、免責按鈕 **min 44×44px**（WCAG 2.5.5）；👍/👎 間距足夠防誤觸。

**引文圖在手機（圖譜可讀性關鍵）**
- 引文卡垂直堆疊；圖 `loading="lazy"` + 佔位；圖高度上限避免占滿螢幕。
- **圖可點放大全螢幕 lightbox**（`CitationImage`→shadcn Dialog）——手機上教科書頁縮圖的標籤/引導線看不清，放大近乎必要（呼應 §5.5 `detail:"high"` 判讀需求）。
- snippet 手機預設截斷 +「展開」。

**捲動**：串流時自動捲到底；使用者上捲閱讀時暫停自動捲、顯示「回到最新」FAB；串流不得造成 layout jank。

**免責同意（手機）**：`DisclaimerModal` 呈 **bottom-sheet／近全螢幕**（非桌面小對話框），按鈕大易點。

**排版**：base ≥16px、中文舒適行高；桌面 `max-w-2xl`～`3xl` 置中、手機全寬加 padding；引文 badge `[Gray, p.812]` inline 自然換行。

**斷點（Tailwind v4 預設）**：base＝手機；`md:`/`lg:` 僅做置中 max-width＋圖上限放寬＋snippet 預設展開。

**驗收目標視窗**：360×640（小 Android）、390×844（iPhone）、768（平板）、≥1024（桌面）；驗 dvh/safe-area／軟鍵盤不遮輸入／圖可放大／觸控目標達標。Playwright 視覺/e2e 列 Phase 13；本 phase 以手動 checklist + 上述目標窗為準。

## 6. 協定保真與 golden

- **dump-golden 解耦**：`frontend/scripts/dump-golden-stream.mjs` 目前輸出（單行 wire record）與後端**手工維護**的
  parts-JSONL golden（`test_api_chat_sse_unit.py` 的 fixture）格式不同卻同路徑 → 跑腳本會蓋掉後端 golden。
  **改**：dump 腳本輸出改寫到獨立檔 `infra/golden/ai_stream_wire_sample.json`（協定參考樣本，含真 SDK wire bytes），
  **不再碰** `ai_stream_golden.jsonl`。可選：加 node 測試解碼 wire sample、交叉檢查後端 part 形狀。
- **後端 golden 更新**（因本 phase 後端 pre-step）：`start` frame 改帶 `messageId`、`data-sources`/`data-verification`
  去掉 `transient:true`。為使 golden 位元組決定性，`turn_id` 產生器在 `chat_event_stream` 設為**可注入**（測試固定值）。

## 7. 技術設定（§7 setup）

- **Tailwind v4**：`@import "tailwindcss"` + `@tailwindcss/postcss`（`postcss.config.mjs`）+ `@theme`（CSS-first、無 `tailwind.config.js`）；
  `app/globals.css` 由 `app/layout.tsx` import；`tw-animate-css` 取代 `tailwindcss-animate`。
- **shadcn/ui**：`npx shadcn@latest init`（new-york、`rsc:true`、`cssVariables:true`）；`@/*` alias 已存在於 tsconfig。
  npm 安裝**需 `--legacy-peer-deps`**（React 19 peer）。逐一 `add` 需要的元件（dialog/button/textarea/alert/card/tooltip…）。
- **next-themes**：暗色模式（`<ThemeProvider attribute="class">`、`@custom-variant dark`）。
- **Next 16**：Turbopack 預設；**不得加自訂 webpack config**（否則 `next build` 失敗）。`next.config.mjs` 維持 `output:"standalone"`。
- **新增套件清單（透明列出）**：
  - runtime：`tailwindcss@^4` `@tailwindcss/postcss` `postcss` `tw-animate-css` `class-variance-authority` `clsx`
    `tailwind-merge` `lucide-react` `next-themes` + shadcn `add` 帶入的 Radix primitives。
  - **dev（測試工具，請點頭確認）**：`vitest` `@testing-library/react` `@testing-library/jest-dom`
    `@testing-library/user-event` `jsdom` `@vitejs/plugin-react`。
- **lockfile**：所有變更提交 `package-lock.json`（D-S）。

## 8. 測試策略

- 元件單元測試（Vitest + Testing Library）：MessageBubble 依 parts 抽取渲染順序、DisclaimerModal 阻擋/持久化、
  FeedbackButtons 送 `turn_id` + 首次👎提示、CitationCard 佔位 fallback、UnverifiedBanner 顯示條件、Composer 禁用態。
- Transport/useChat：以 mock fetch 回傳罐裝 UI-message-stream SSE（重用 golden 形 frames）驅動 useChat，斷言
  「先 sources 後 text」、verification、error chunk → error 狀態。
- 協定交叉檢查：node 解碼 dump wire sample、比對後端 part 形狀（解耦後的參考檔）。
- smoke：`next build`（**pin-verify TS 6.0 ↔ Next 16 型別檢查**，見 §10）。
- **不改** `tests/golden_qa.jsonl`、不碰 `eval_thresholds.yaml`。

## 9. 後端 pre-step（Phase 10 內，最小且可逆）

> 屬 autonomy 授權的 DB/技術修正，但觸及 §5.6/§5.7/D-H DECIDED 區 → 記 **DL-027**。

1. **migration 008**：`query_logs` 加 `turn_id UUID`（nullable，向後相容）+ 建 `UNIQUE` 索引（Postgres 允許多 NULL；供 feedback 以 turn_id 查詢）；可逆 downgrade 落欄。
2. **chat.py**：`chat_event_stream` 頂端生 `turn_id`（可注入產生器，測試固定）；所有 `start_part()` → `start_part(str(turn_id))`；
   每處 `deps.log_query(...)` 帶 `turn_id`；2 個 `data_part(...)` 加 `transient=False`。
3. **main.py**：`_log_query` 收 `turn_id` 並寫入新欄；`_write_feedback` 改 `UPDATE … WHERE turn_id=$::uuid AND user_id=$::uuid`。
4. **feedback.py**：body 改 `{message_id, rating, text}`（`message_id` 即 turn_id，驗 UUID）；`apply_feedback` 串接。
5. **golden**：更新 `ai_stream_golden.jsonl`（messageId + 去 transient）；對應 SSE 序測試同步。
6. **任何 LLM/檢索核心皆不動**；RAGAS 不受影響（純前端＋記錄欄位）。

## 10. 風險與 pin-verify（實作時查證）

- `useChat` 是否以 `start.messageId` 設 `message.id`（否→ §4 fallback）。
- UI-message-stream `error` chunk → useChat `error` 狀態映射。
- TS 6.0.3 ↔ Next 16 型別檢查相容（research 未確認上限）→ `next build`/`tsc` smoke；不相容屬 Next↔TS 議題、與 UI stack 無關。
- shadcn CLI 寫出的 `@custom-variant dark` 語法因 CLI 版本微異 → 以實際產出為準。
- npm peer 警告為預期（React 19）→ `--legacy-peer-deps`。

## 11. 完成定義（對齊 roadmap Phase 10 驗收）

對後端/mock SSE：先顯示引用面板 → 串流文字 → 完成；免責視窗持久化；底部浮水印＋引文清單；👍/👎＋文字寫入**單一回合**；
首次倒讚提示回報；未驗證引文顯示 banner；全繁體中文；DL-027 已記。
**手機為主（§5.1）**：360/390 寬下 dvh 滿版、軟鍵盤不遮輸入、引文圖可點放大、觸控目標 ≥44px、safe-area 不被裁切；桌面置中加寬。
