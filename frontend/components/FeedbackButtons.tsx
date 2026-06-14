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

type Phase =
  | "idle"
  | "submitting"           // M6: in-flight upvote — buttons disabled
  | "downvote-open"        // 👎 panel expanded
  | "submitting-downvote"  // M6: in-flight downvote
  | "submitted";           // terminal: locked

/**
 * 每回合回饋按鈕 (§6.7)
 * 👍 立即送出；👎 展開可選文字框（首次顯示回報機制說明）。
 * 送出後顯示確認訊息並鎖定，防止重複送出。
 *
 * M6 fixes:
 *   - Transitions to "submitting" BEFORE awaiting postFeedback → blocks re-entry
 *   - Catches rejected promises → shows 繁中 error banner INLINE (buttons stay
 *     visible so the user can retry); no unhandled rejections
 */
export function FeedbackButtons({
  messageId,
  className,
}: FeedbackButtonsProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [showHint, setShowHint] = useState(false);
  const [text, setText] = useState("");
  /** Inline error message shown without hiding the retry buttons. */
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (phase === "submitted") {
    return (
      <p
        className={cn("text-xs text-muted-foreground", className)}
        aria-live="polite"
      >
        已收到回饋
      </p>
    );
  }

  const isSubmitting = phase === "submitting" || phase === "submitting-downvote";

  const handleUpvote = async () => {
    // M6: guard re-entry — transition state BEFORE await
    if (isSubmitting) return;
    setPhase("submitting");
    setErrorMsg(null);
    try {
      await postFeedback({ messageId, rating: 1 });
      setPhase("submitted");
    } catch {
      // Show error inline; reset to idle so buttons remain clickable (retry)
      setPhase("idle");
      setErrorMsg("提交回饋失敗，請重試");
    }
  };

  const handleDownvoteOpen = () => {
    if (isSubmitting) return;
    const shouldShow = shouldPromptFirstDownvote();
    if (shouldShow) markFirstDownvotePrompted();
    setShowHint(shouldShow);
    setPhase("downvote-open");
    setErrorMsg(null);
  };

  const handleDownvoteSubmit = async () => {
    setPhase("submitting-downvote");
    setErrorMsg(null);
    try {
      await postFeedback({
        messageId,
        rating: -1,
        ...(text.trim() ? { text: text.trim() } : {}),
      });
      setPhase("submitted");
    } catch {
      setPhase("downvote-open");
      setErrorMsg("提交回饋失敗，請重試");
    }
  };

  return (
    <div className={cn("space-y-2", className)}>
      {/* Inline error notice (M6) — shown without replacing the buttons */}
      {errorMsg && (
        <p className="text-xs text-destructive" aria-live="assertive">
          {errorMsg}
        </p>
      )}

      {/* 主按鈕列 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label="這則回答有幫助"
          title="有幫助"
          disabled={isSubmitting}
          className={cn(
            "flex min-h-11 min-w-11 items-center justify-center rounded-md",
            "border border-border text-base transition-colors",
            "hover:border-anatomy-accent hover:bg-anatomy-accent-subtle",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "active:scale-95",
            "disabled:pointer-events-none disabled:opacity-50",
          )}
          onClick={handleUpvote}
        >
          👍
        </button>

        <button
          type="button"
          aria-label="這則回答需要改善"
          title="需要改善"
          disabled={isSubmitting}
          className={cn(
            "flex min-h-11 min-w-11 items-center justify-center rounded-md",
            "border border-border text-base transition-colors",
            "hover:border-destructive hover:bg-destructive/10",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "active:scale-95",
            "disabled:pointer-events-none disabled:opacity-50",
            phase === "downvote-open" && "border-destructive bg-destructive/10",
          )}
          onClick={handleDownvoteOpen}
        >
          👎
        </button>
      </div>

      {/* 展開的回饋文字框 */}
      {(phase === "downvote-open" || phase === "submitting-downvote") && (
        <div className="space-y-2 rounded-lg border border-border bg-muted/30 p-3">
          {/* 首次回報機制說明 */}
          {showHint && (
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
            disabled={phase === "submitting-downvote"}
          />

          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="min-h-11 min-w-[60px]"
              onClick={() => setPhase("idle")}
              disabled={phase === "submitting-downvote"}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              className="min-h-11 min-w-[60px]"
              onClick={handleDownvoteSubmit}
              disabled={phase === "submitting-downvote"}
            >
              送出
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
