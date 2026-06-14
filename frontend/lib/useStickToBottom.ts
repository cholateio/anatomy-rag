"use client";

import { useRef, useEffect, useState, useCallback } from "react";

/** Distance from the bottom (px) within which we consider the container "stuck". */
export const STICK_THRESHOLD = 48;

/**
 * Auto-scroll hook for streaming chat lists.
 *
 * - Attaches a scroll listener to `containerRef` (the outer scrollable div).
 * - H4 fix: attaches ResizeObserver to `innerRef` (the INNER growing content
 *   div) rather than the outer container.  The outer container's box size does
 *   not change when content is appended — only the inner div's height grows —
 *   so watching the outer box never fires during streaming.
 * - When the user is within `STICK_THRESHOLD` px of the bottom, auto-scrolls
 *   the container whenever the inner content grows.
 * - When the user scrolls up, `showJumpToLatest` becomes `true` and
 *   `jumpToLatest()` lets them snap back.
 *
 * Usage in MessageList:
 *   const { containerRef, innerRef, ... } = useStickToBottom();
 *   <div ref={containerRef} className="overflow-y-auto ...">
 *     <div ref={innerRef}>  ← inner growing content
 *       {messages.map(...)}
 *     </div>
 *   </div>
 */
export function useStickToBottom() {
  const containerRef = useRef<HTMLDivElement>(null);
  /** Attach to the growing INNER content div (H4). */
  const innerRef = useRef<HTMLDivElement>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  // Track stick state without triggering re-renders on every scroll pixel
  const isStuck = useRef(true);

  const jumpToLatest = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight - el.clientHeight;
    isStuck.current = true;
    setShowJumpToLatest(false);
  }, []);

  /**
   * Belt-and-suspenders explicit scroll.
   * Call this in a `useEffect` keyed on content changes (e.g. messages.length +
   * last-message text length) to cover streaming token appends where
   * ResizeObserver fires late or is not available (e.g. jsdom test env).
   */
  const scrollToBottomIfStuck = useCallback(() => {
    const el = containerRef.current;
    if (!el || !isStuck.current) return;
    el.scrollTop = el.scrollHeight - el.clientHeight;
  }, []);

  // Scroll event → decide whether to show the FAB
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.clientHeight - el.scrollTop;
      const atBottom = distFromBottom <= STICK_THRESHOLD;
      isStuck.current = atBottom;
      setShowJumpToLatest(!atBottom);
    };
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  // H4: Observe the INNER growing content element (not the outer container).
  // The outer scroll container's own size is fixed; only the inner div grows
  // as streaming tokens arrive.  Falls back to the container itself when no
  // innerRef is provided (e.g. in test harnesses that only use containerRef).
  useEffect(() => {
    const container = containerRef.current;
    const target = innerRef.current ?? containerRef.current;
    if (!container || !target) return;

    const tryScroll = () => {
      if (isStuck.current) {
        container.scrollTop = container.scrollHeight - container.clientHeight;
      }
    };

    const ro = new ResizeObserver(tryScroll);
    ro.observe(target);
    return () => ro.disconnect();
  }, []);

  return { containerRef, innerRef, showJumpToLatest, jumpToLatest, scrollToBottomIfStuck };
}
