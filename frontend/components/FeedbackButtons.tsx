"use client";

import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { postFeedback } from "@/lib/api";
import {
  shouldPromptFirstDownvote,
  markFirstDownvotePrompted,
} from "@/lib/disclaimer";
import { cn } from "@/lib/utils";

interface FeedbackButtonsProps {
  messageId: string;
  className?: string;
}

type State =
  | { phase: "idle" }
  | { phase: "downvote-open"; showHint: boolean }
  | { phase: "submitted" };

/**
 * 每回合回饋按鈕 (§6.7)
 * 👍 立即送出；👎 展開可選文字框（首次顯示回報機制說明）。
 * 送出後顯示確認訊息並鎖定，防止重複送出。
 */
export function FeedbackButtons({
  messageId,
  className,
}: FeedbackButtonsProps) {
  const [state, setState] = useState<State>({ phase: "idle" });
  const [text, setText] = useState("");

  if (state.phase === "submitted") {
    return (
      <p
        className={cn(
          "text-xs text-muted-foreground",
          className,
        )}
        aria-live="polite"
      >
        已收到回饋
      </p>
    );
  }

  const handleUpvote = async () => {
    await postFeedback({ messageId, rating: 1 });
    setState({ phase: "submitted" });
  };

  const handleDownvoteOpen = () => {
    const showHint = shouldPromptFirstDownvote();
    if (showHint) markFirstDownvotePrompted();
    setState({ phase: "downvote-open", showHint });
  };

  const handleDownvoteSubmit = async () => {
    await postFeedback({
      messageId,
      rating: -1,
      ...(text.trim() ? { text: text.trim() } : {}),
    });
    setState({ phase: "submitted" });
  };

  return (
    <div className={cn("space-y-2", className)}>
      {/* 主按鈕列 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label="這則回答有幫助"
          title="有幫助"
          className={cn(
            "flex min-h-11 min-w-11 items-center justify-center rounded-md",
            "border border-border text-base transition-colors",
            "hover:border-anatomy-accent hover:bg-anatomy-accent-subtle",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "active:scale-95",
          )}
          onClick={handleUpvote}
        >
          👍
        </button>

        <button
          type="button"
          aria-label="這則回答需要改善"
          title="需要改善"
          className={cn(
            "flex min-h-11 min-w-11 items-center justify-center rounded-md",
            "border border-border text-base transition-colors",
            "hover:border-destructive hover:bg-destructive/10",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "active:scale-95",
            state.phase === "downvote-open" &&
              "border-destructive bg-destructive/10",
          )}
          onClick={handleDownvoteOpen}
        >
          👎
        </button>
      </div>

      {/* 展開的回饋文字框 */}
      {state.phase === "downvote-open" && (
        <div className="space-y-2 rounded-lg border border-border bg-muted/30 p-3">
          {/* 首次回報機制說明 */}
          {state.showHint && (
            <p className="text-xs leading-relaxed text-muted-foreground">
              您的回報有助於持續改善系統品質。內容包含回報的頁碼、問題描述等，均受隱私保護。
            </p>
          )}

          <Textarea
            placeholder="請描述問題（選填）：例如頁碼錯誤、引文不相關…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={2}
            className="min-h-[44px] resize-none text-base"
            aria-label="回饋說明"
          />

          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="min-h-11 min-w-[60px]"
              onClick={() => setState({ phase: "idle" })}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              className="min-h-11 min-w-[60px]"
              onClick={handleDownvoteSubmit}
            >
              送出
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
