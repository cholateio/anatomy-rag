// Phase 0 骨架頁面 — 僅供基礎設施冒煙測試使用
// 真正的 /chat 問答介面（useChat + SSE 串流）將於 Phase 8 實作

export default function HomePage() {
  return (
    <main style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <h1>系統骨架運行中</h1>
      <p>
        這是 Phase 0 開發骨架，僅用於驗證基礎設施可正常啟動。
        真正的問答介面（/chat，含串流回應與引文）將於 Phase 8 實作。
      </p>
    </main>
  );
}
