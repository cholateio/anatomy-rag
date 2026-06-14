import { CitationCard } from "@/components/CitationCard";
import { cn } from "@/lib/utils";
import type { SourcesData } from "@/lib/types";

interface CitationPanelProps {
  data: SourcesData;
  className?: string;
}

/**
 * 引用面板 — 聚合多張 CitationCard
 * 空 sources 時靜默不渲染；有 sources 則顯示計數標題 + 逐張卡片。
 */
export function CitationPanel({ data, className }: CitationPanelProps) {
  if (!data.sources || data.sources.length === 0) {
    // §6.7 強制引文清單: show explicit empty-state rather than silently rendering nothing
    return (
      <p className="text-xs italic text-muted-foreground">
        本回答未引用教科書頁面
      </p>
    );
  }

  return (
    <section aria-label="引用來源" className={cn("space-y-3", className)}>
      {/* 標題：書架圖示 + 引用計數，呼應學術參考文獻感 */}
      <div className="flex items-center gap-1.5">
        <span aria-hidden="true" className="text-base">📚</span>
        <h2 className="text-sm font-semibold tracking-wide text-muted-foreground">
          引用 ({data.sources.length})
        </h2>
        {/* 細線延伸 */}
        <div className="h-px flex-1 bg-border" />
      </div>

      {/* Citation 卡片列表 */}
      <div className="space-y-2.5">
        {data.sources.map((c, i) => (
          <CitationCard key={`${c.book_title}-${c.page}-${i}`} c={c} />
        ))}
      </div>
    </section>
  );
}
