import { cn } from "@/lib/utils";

interface EmptyStateProps {
  onPick: (q: string) => void;
  className?: string;
}

/** 三個代表性解剖學例題，引導學生快速開始提問。 */
const EXAMPLE_QUESTIONS = [
  "臂叢神經的組成與分布為何？",
  "心臟的冠狀動脈如何供血至各區域？",
  "腹腔神經叢的位置與功能為何？",
] as const;

/**
 * 空白狀態引導 (Empty State)
 * 顯示三個精選解剖題讓學生快速開始；按鈕觸發 onPick 填入輸入框。
 * 設計靈感：教科書封面的「本章重點」小框。
 */
export function EmptyState({ onPick, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "mx-auto flex max-w-lg flex-col items-center px-4 py-16 text-center",
        className,
      )}
    >
      {/* 主圖示 — 解剖書符號 */}
      <div
        className={cn(
          "mb-6 flex size-16 items-center justify-center rounded-2xl",
          "bg-anatomy-accent-subtle text-3xl",
          "border border-anatomy-accent/20",
        )}
        aria-hidden="true"
      >
        🫀
      </div>

      <h2 className="mb-1.5 font-serif text-xl font-semibold text-foreground">
        解剖學 RAG
      </h2>
      <p className="mb-8 text-sm leading-relaxed text-muted-foreground">
        以教科書為基礎的解剖學問答系統。
        <br />
        試著詢問以下問題：
      </p>

      {/* 例題按鈕 — 每個 ≥44px 且有完整 accessible label */}
      <div className="w-full space-y-2.5">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className={cn(
              "group w-full rounded-xl border border-border bg-card",
              "min-h-[52px] px-4 py-3 text-left text-sm",
              "text-foreground/80 transition-all",
              "hover:border-anatomy-accent/40 hover:bg-anatomy-accent-subtle hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              "active:scale-[0.99]",
            )}
          >
            <span className="mr-2 font-mono text-xs text-anatomy-accent opacity-70 group-hover:opacity-100">
              →
            </span>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
