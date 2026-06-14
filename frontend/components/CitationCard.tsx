"use client";

import React, { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { CitationImage } from "@/components/CitationImage";
import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/types";

interface CitationCardProps {
  c: Citation;
  className?: string;
}

/**
 * 單一引文卡片
 * 呈現書名、版次、頁碼、圖號、節錄文字（可展開）以及縮圖。
 * 頁碼使用等寬字型 + 解剖強調色，呼應圖譜標籤排版。
 */
export function CitationCard({ c, className }: CitationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const SNIPPET_LIMIT = 120;
  const isLong = c.snippet.length > SNIPPET_LIMIT;
  const displaySnippet =
    expanded || !isLong ? c.snippet : c.snippet.slice(0, SNIPPET_LIMIT) + "…";

  return (
    <Card
      className={cn(
        "gap-3 py-4 text-sm shadow-none",
        "border-border/70",
        className,
      )}
    >
      <CardContent className="space-y-3 px-4">
        {/* 書名列 + 頁碼 */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="font-medium leading-snug text-foreground">
              {c.book_title}
              {c.edition && (
                <span className="ml-1 text-xs font-normal text-muted-foreground">
                  （{c.edition}）
                </span>
              )}
            </p>
            {c.figure && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                圖 {c.figure}
              </p>
            )}
          </div>

          {/* 頁碼 — 等寬、強調色，如同圖譜標記 */}
          <span
            className={cn(
              "shrink-0 font-mono text-[13px] font-medium leading-none",
              "rounded-sm px-1.5 py-0.5",
              "bg-anatomy-accent-subtle text-anatomy-accent",
            )}
            aria-label={`第 ${c.page} 頁`}
          >
            p.{c.page}
          </span>
        </div>

        {/* 節錄文字 + 展開／收起 */}
        <div>
          <p className="leading-relaxed text-muted-foreground">
            {displaySnippet}
          </p>
          {isLong && (
            <button
              type="button"
              className={cn(
                "mt-1 min-h-[44px] text-xs text-anatomy-accent underline-offset-2",
                "hover:underline focus-visible:outline-none focus-visible:ring-2",
                "focus-visible:ring-ring focus-visible:ring-offset-1",
                "flex items-center",
              )}
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
            >
              {expanded ? "收起" : "展開"}
            </button>
          )}
        </div>

        {/* 縮圖 */}
        <CitationImage src={c.image_url} alt={`${c.book_title} 第${c.page}頁`} />
      </CardContent>
    </Card>
  );
}
