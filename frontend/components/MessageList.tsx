import { useEffect } from "react";
import { ChevronDown } from "lucide-react";
import { isTextUIPart } from "ai";
import { EmptyState } from "@/components/EmptyState";
import { MessageBubble } from "@/components/MessageBubble";
import { cn } from "@/lib/utils";
import { useStickToBottom } from "@/lib/useStickToBottom";
import type { AnatomyUIMessage } from "@/lib/types";

type Status = "submitted" | "streaming" | "ready" | "error";

interface MessageListProps {
  messages: AnatomyUIMessage[];
  status: Status;
  onPickExample: (q: string) => void;
  className?: string;
}

/**
 * Scrollable message list with auto-stick-to-bottom behaviour.
 *
 * - Empty → shows EmptyState with example prompts (inside the innerRef div).
 * - Non-empty → maps each message to MessageBubble.
 * - H4 fix: `<div ref={innerRef}>` is ALWAYS mounted (even when messages is
 *   empty), so ResizeObserver attaches to the growing element on mount rather
 *   than falling back to the outer container.  When the first message arrives
 *   and its text grows during streaming, the observer fires correctly.
 * - Belt-and-suspenders: a `useEffect` keyed on content changes calls
 *   `scrollToBottomIfStuck()` explicitly, covering cases where ResizeObserver
 *   fires late or not at all (e.g. jsdom test environment).
 * - M7: only the LAST assistant message receives isStreaming=true so the
 *   streaming cursor never appears on earlier turns during a follow-up.
 * - When the user scrolls up, a 「回到最新」 FAB appears in the bottom-right;
 *   clicking it snaps back and re-enables auto-scroll.
 */
export function MessageList({ messages, status, onPickExample, className }: MessageListProps) {
  const { containerRef, innerRef, showJumpToLatest, jumpToLatest, scrollToBottomIfStuck } =
    useStickToBottom();

  // M7: find the index of the last assistant message to target the cursor
  const lastAssistantIndex = messages.reduce<number>(
    (lastIdx, m, idx) => (m.role === "assistant" ? idx : lastIdx),
    -1,
  );

  // Derive a content signature that changes on every streaming token append.
  // messages.length catches message-count changes; lastTextLen catches
  // token-by-token growth of the last message during streaming.
  const lastMsg = messages[messages.length - 1];
  const lastTextLen = (lastMsg?.parts ?? [])
    .filter(isTextUIPart)
    .reduce((n, p) => n + p.text.length, 0);

  // Belt-and-suspenders: explicit scroll on content change.
  // scrollToBottomIfStuck is a stable useCallback (never changes reference).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { scrollToBottomIfStuck(); }, [messages.length, lastTextLen]);

  return (
    <div className={cn("relative flex-1 overflow-hidden", className)}>
      {/* Scrollable container */}
      <div
        ref={containerRef}
        className="h-full overflow-y-auto pb-2 scroll-smooth"
      >
        {/*
          H4 fix: innerRef div is ALWAYS rendered so ResizeObserver attaches
          to the growing element from mount (even when showing EmptyState).
          EmptyState lives inside it when messages is empty; MessageBubbles
          replace it when messages arrive.
        */}
        <div ref={innerRef} className="flex flex-col divide-y divide-border/20 py-2">
          {messages.length === 0 ? (
            <EmptyState
              onPick={onPickExample}
              className="my-auto"
            />
          ) : (
            messages.map((m, idx) => (
              <MessageBubble
                key={m.id}
                message={m}
                status={status}
                // M7: only the last assistant message is "streaming"
                isStreaming={status === "streaming" && idx === lastAssistantIndex}
              />
            ))
          )}
        </div>
      </div>

      {/* Jump-to-latest FAB ─────────────────────────────────────────────── */}
      {showJumpToLatest && (
        <button
          type="button"
          onClick={jumpToLatest}
          aria-label="回到最新訊息"
          className={cn(
            "absolute bottom-4 right-4 z-20",
            "flex items-center gap-1.5 rounded-full",
            "bg-anatomy-accent text-anatomy-accent-fg",
            "px-3 py-2 text-xs font-medium shadow-lg",
            "min-h-[44px]",
            "transition-all hover:bg-anatomy-accent/90 active:scale-95",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          )}
        >
          <ChevronDown className="size-3.5" aria-hidden="true" />
          回到最新
        </button>
      )}
    </div>
  );
}
