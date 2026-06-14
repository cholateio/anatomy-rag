"use client";

import { useRef, useEffect, useState, useCallback } from "react";

/** Distance from the bottom (px) within which we consider the container "stuck". */
export const STICK_THRESHOLD = 48;

/**
 * Auto-scroll hook for streaming chat lists.
 *
 * - Attaches a scroll listener to the returned `containerRef`.
 * - When the user is within `STICK_THRESHOLD` px of the bottom, the container
 *   auto-scrolls as new content arrives (via ResizeObserver).
 * - When the user scrolls up, `showJumpToLatest` becomes `true` and
 *   `jumpToLatest()` lets them snap back.
 */
export function useStickToBottom() {
  const containerRef = useRef<HTMLDivElement>(null);
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

  // When content height grows (new streaming tokens), auto-scroll if stuck
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const tryScroll = () => {
      if (isStuck.current) {
        el.scrollTop = el.scrollHeight - el.clientHeight;
      }
    };
    const ro = new ResizeObserver(tryScroll);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return { containerRef, showJumpToLatest, jumpToLatest };
}
