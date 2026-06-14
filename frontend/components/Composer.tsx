"use client";

import React, { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { SendHorizonal } from "lucide-react";
import { cn } from "@/lib/utils";

type Status = "submitted" | "streaming" | "ready" | "error";

interface ComposerProps {
  onSend: (text: string) => void;
  status: Status;
  className?: string;
}

/**
 * 訊息輸入框 (R7)
 * 黏在底部；Shift+Enter 換行，Enter 送出；text-base 防止 iOS 自動縮放。
 * 使用 field-sizing-content（瀏覽器原生自動調高）配合 max-h 上限後可捲動。
 */
export function Composer({ onSend, status, className }: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isReady = status === "ready";

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed || !isReady) return;
    onSend(trimmed);
    setValue("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const statusLabel: Record<Status, string> = {
    submitted: "已送出，等待回應中…",
    streaming: "回答串流中…",
    ready: "",
    error: "發生錯誤，請重試",
  };

  return (
    <div
      className={cn(
        "sticky bottom-0 z-30 w-full bg-background/95 backdrop-blur-sm",
        "pb-[env(safe-area-inset-bottom)]",
        className,
      )}
    >
      {/* 細線分隔 */}
      <div className="border-t border-border" />

      <div className="px-4 py-3 sm:px-6">
        {/* 狀態提示（非 ready 時顯示） */}
        {status !== "ready" && statusLabel[status] && (
          <p
            className="mb-1.5 text-xs text-muted-foreground"
            aria-live="polite"
          >
            {statusLabel[status]}
          </p>
        )}

        <div className="flex items-end gap-2">
          <Textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="輸入解剖學問題…（Shift+Enter 換行）"
            rows={1}
            // text-base prevents iOS auto-zoom on focus; field-sizing-content auto-grows
            className={cn(
              "min-h-11 max-h-40 flex-1 resize-none text-base",
              "py-2.5 leading-snug",
            )}
            enterKeyHint="send"
            aria-label="輸入問題"
            disabled={!isReady}
          />

          <Button
            type="button"
            size="icon"
            className={cn(
              "min-h-11 min-w-11 shrink-0",
              "bg-anatomy-accent text-anatomy-accent-fg",
              "hover:bg-anatomy-accent/90",
              "focus-visible:ring-anatomy-accent/50",
              "disabled:opacity-40",
            )}
            aria-label="送出"
            onClick={handleSend}
            disabled={!isReady || !value.trim()}
          >
            <SendHorizonal className="size-4" />
            <span className="sr-only">送出</span>
          </Button>
        </div>

        <p className="mt-1.5 text-center text-[10px] text-muted-foreground/50">
          Shift+Enter 換行 · Enter 送出
        </p>
      </div>
    </div>
  );
}
