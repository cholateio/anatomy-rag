/**
 * dump-golden-stream.mjs
 *
 * 用已安裝的 ai@6.0.197 套件，產生一段代表性的 UI Message Stream，
 * 並將真實 SSE wire bytes 寫入 infra/golden/ai_stream_wire_sample.json（協定參考樣本）。
 *
 * 此檔為「真 SDK wire 協定參考樣本」（DL-018 / DL-027）。
 * ⚠️ 後端 emitter 的位元組對照基準是 infra/golden/ai_stream_golden.jsonl，
 *    由 backend/tests/test_api_chat_sse_unit.py 手工維護——本腳本 MUST NOT 覆寫它。
 *
 * 執行：cd frontend && node scripts/dump-golden-stream.mjs
 *
 * 已驗證的 API（ai@6.0.197）：
 *   - createUIMessageStream({ execute }): ReadableStream<UIMessageChunk>
 *     execute 接收 { writer }，writer.write(part) 將 chunk 推入串流
 *   - createUIMessageStreamResponse({ stream }): Response
 *     將 UIMessageChunk 串流轉換為 SSE Response（text/event-stream）
 *   - 每個 chunk 序列化為 `data: ${JSON.stringify(part)}\n\n`
 *   - 串流結尾附加 `data: [DONE]\n\n`
 *
 * UIMessageChunk 已確認的 type 值（來自 dist/index.d.ts + index.mjs）：
 *   { type: 'start', messageId?: string, messageMetadata?: unknown }
 *   { type: 'data-${NAME}', id?: string, data: unknown, transient?: boolean }
 *   { type: 'text-start', id: string }
 *   { type: 'text-delta', id: string, delta: string }
 *   { type: 'text-end', id: string }
 *   { type: 'finish', finishReason?: string, messageMetadata?: unknown }
 */

import { createUIMessageStream, createUIMessageStreamResponse } from "ai";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

// ---------- 代表性的 citation 物件（後端 data-sources 的 payload 形狀為 { sources: [...] }）----------
const EXAMPLE_CITATIONS = [
  {
    book_title: "Gray42",
    page: 812,
    figure: "Fig.7-23",
    image_url: "/p/1.webp",
    snippet: "肱二頭肌起於肩胛骨喙突…",
    score: 0.91,
  },
  {
    book_title: "Netter8",
    page: 401,
    figure: "Plate 401",
    image_url: "/p/2.webp",
    snippet: "Biceps brachii: short head from coracoid process…",
    score: 0.87,
  },
];

// ---------- 建立 UI Message Stream ----------
// execute 同步寫入所有 chunk（無 await），async 宣告僅為符合型別簽章
const stream = createUIMessageStream({
  execute: async ({ writer }) => {
    // 1. 串流開始
    writer.write({ type: "start" });

    // 2. 自訂 data part：傳遞引文清單給前端
    //    type 格式為 `data-${NAME}`，payload 放在 data 欄位；後端契約 key 為 sources（非 citations）
    writer.write({
      type: "data-sources",
      data: { sources: EXAMPLE_CITATIONS },
    });

    // 3. 文字串流（text-start → text-delta(s) → text-end）
    writer.write({ type: "text-start", id: "t0" });
    writer.write({ type: "text-delta", id: "t0", delta: "肱二頭肌" });
    writer.write({
      type: "text-delta",
      id: "t0",
      delta: "起於肩胛骨喙突 [Gray42, p.812, Fig.7-23]。",
    });
    writer.write({
      type: "text-delta",
      id: "t0",
      delta: " Biceps brachii originates from the coracoid process [Netter8, p.401, Plate 401].",
    });
    writer.write({ type: "text-end", id: "t0" });

    // 4. 串流結束
    writer.write({ type: "finish" });
  },
});

// ---------- 將 UIMessageChunk 串流轉為 SSE Response ----------
const res = createUIMessageStreamResponse({ stream });

// ---------- 讀取 Response body 取得真實 wire bytes ----------
// Node.js v21+ 內建的全域 Response 支援 .text()
// 在 ai 套件中，body 已經過 JsonToSseTransformStream + TextEncoderStream 處理
const wireBytes = await res.text();

// ---------- 輸出至 stdout（供人工確認）----------
console.log("=== Vercel AI SDK UI Message Stream wire bytes ===");
console.log(wireBytes);
console.log("=== end ===");

// ---------- 寫入協定參考樣本（NOT 後端 golden）----------
const __dirname = dirname(fileURLToPath(import.meta.url));
const outputPath = join(__dirname, "../../infra/golden/ai_stream_wire_sample.json");

const record = {
  // 真 SDK 的 SSE wire bytes（後端手刻 emitter 的 framing 應與此一致：data: <json>\n\n、data: [DONE]\n\n）
  wire: wireBytes,
  // 產生此樣本的 ai 套件版本（凍結基準）
  ai_version: "6.0.197",
  // 記錄各 chunk type 的欄位名稱，供後端 emitter 快速查閱
  schema_notes: {
    start:      "{ type: 'start', messageId?: string }",
    data_part:  "{ type: 'data-${NAME}', data: unknown, id?: string, transient?: boolean }",
    text_start: "{ type: 'text-start', id: string }",
    text_delta: "{ type: 'text-delta', id: string, delta: string }",
    text_end:   "{ type: 'text-end', id: string }",
    finish:     "{ type: 'finish', finishReason?: string }",
    sse_line:   "data: ${JSON.stringify(chunk)}\\n\\n",
    sse_done:   "data: [DONE]\\n\\n",
  },
};

writeFileSync(outputPath, JSON.stringify(record) + "\n", "utf-8");
console.log(`\n✔ wire 協定樣本已寫入：${outputPath}`);
