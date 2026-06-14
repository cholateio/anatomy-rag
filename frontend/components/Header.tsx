import { cn } from "@/lib/utils";

/**
 * 全域標題列 (R8)
 * 雙細線底邊 — 引自解剖圖譜章節分隔樣式
 */
export function Header({ className }: { className?: string }) {
  return (
    <header
      className={cn(
        "sticky top-0 z-40 w-full bg-background/95 backdrop-blur-sm pt-[env(safe-area-inset-top)]",
        className,
      )}
    >
      <div className="flex items-center justify-between px-4 py-2.5 sm:px-6">
        {/* 主標題：serif 字型呼應學術圖譜質感 */}
        <h1 className="font-serif text-lg font-semibold tracking-tight text-foreground">
          解剖學 RAG
        </h1>

        {/* 教育用途 badge — 小型、靜默，始終可見 */}
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2.5 py-0.5",
            "bg-anatomy-accent-subtle border border-anatomy-accent/20",
            "text-[11px] font-medium leading-none tracking-wide text-anatomy-accent",
          )}
        >
          教育用途
        </span>
      </div>

      {/* 雙細線：主線 + 輔線 */}
      <div className="border-t border-border" />
      <div className="border-t border-border/40" />
    </header>
  );
}
