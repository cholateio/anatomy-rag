import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import React from "react";
import { useStickToBottom, STICK_THRESHOLD } from "@/lib/useStickToBottom";

// A thin test harness (no innerRef) — for existing scroll/FAB tests
function TestHarness() {
  const { containerRef, showJumpToLatest, jumpToLatest } = useStickToBottom();
  return React.createElement(
    "div",
    { ref: containerRef, "data-testid": "container" },
    React.createElement("span", { "data-testid": "jump-state" }, showJumpToLatest ? "show" : "hide"),
    React.createElement("button", { "data-testid": "jump-btn", onClick: jumpToLatest }, "Jump"),
  );
}

// A harness that also wires innerRef — for H4 ResizeObserver tests
function TestHarnessWithInner() {
  const { containerRef, innerRef, showJumpToLatest, jumpToLatest } = useStickToBottom();
  return React.createElement(
    "div",
    { ref: containerRef, "data-testid": "container" },
    React.createElement("div", { ref: innerRef, "data-testid": "inner" }, "content"),
    React.createElement("span", { "data-testid": "jump-state" }, showJumpToLatest ? "show" : "hide"),
    React.createElement("button", { "data-testid": "jump-btn", onClick: jumpToLatest }, "Jump"),
  );
}

/** Install mocked scroll props on an element and return a getter for scrollTop. */
function mockScrollProps(
  el: Element,
  init: { scrollTop: number; scrollHeight: number; clientHeight: number },
) {
  let currentScrollTop = init.scrollTop;
  Object.defineProperty(el, "scrollHeight", { value: init.scrollHeight, configurable: true, writable: false });
  Object.defineProperty(el, "clientHeight", { value: init.clientHeight, configurable: true, writable: false });
  Object.defineProperty(el, "scrollTop", {
    get: () => currentScrollTop,
    set: (v: number) => { currentScrollTop = v; },
    configurable: true,
  });
  return { getScrollTop: () => currentScrollTop };
}

describe("useStickToBottom", () => {
  it("starts with showJumpToLatest=false (no initial scroll)", () => {
    render(React.createElement(TestHarness));
    expect(screen.getByTestId("jump-state").textContent).toBe("hide");
  });

  it("keeps showJumpToLatest=false when scrolled at the very bottom", () => {
    render(React.createElement(TestHarness));
    const el = screen.getByTestId("container");
    // distFromBottom = 1000 - 100 - 900 = 0 → at bottom
    mockScrollProps(el, { scrollTop: 900, scrollHeight: 1000, clientHeight: 100 });
    fireEvent.scroll(el);
    expect(screen.getByTestId("jump-state").textContent).toBe("hide");
  });

  it("keeps showJumpToLatest=false within the threshold boundary", () => {
    render(React.createElement(TestHarness));
    const el = screen.getByTestId("container");
    // distFromBottom = STICK_THRESHOLD - 1 → still at-bottom
    const scrollTop = 1000 - 100 - (STICK_THRESHOLD - 1);
    mockScrollProps(el, { scrollTop, scrollHeight: 1000, clientHeight: 100 });
    fireEvent.scroll(el);
    expect(screen.getByTestId("jump-state").textContent).toBe("hide");
  });

  it("shows showJumpToLatest=true when user scrolls up beyond threshold", () => {
    render(React.createElement(TestHarness));
    const el = screen.getByTestId("container");
    // distFromBottom = 900 >> STICK_THRESHOLD → scrolled up
    mockScrollProps(el, { scrollTop: 0, scrollHeight: 1000, clientHeight: 100 });
    fireEvent.scroll(el);
    expect(screen.getByTestId("jump-state").textContent).toBe("show");
  });

  it("jumpToLatest scrolls el to the bottom and resets showJumpToLatest", () => {
    render(React.createElement(TestHarness));
    const el = screen.getByTestId("container");
    const { getScrollTop } = mockScrollProps(el, { scrollTop: 0, scrollHeight: 1000, clientHeight: 100 });

    // Scroll up to show the FAB
    fireEvent.scroll(el);
    expect(screen.getByTestId("jump-state").textContent).toBe("show");

    // Click jump button
    act(() => {
      fireEvent.click(screen.getByTestId("jump-btn"));
    });

    // scrollTop should be set to scrollHeight - clientHeight = 900
    expect(getScrollTop()).toBe(900);
    // The FAB should disappear
    expect(screen.getByTestId("jump-state").textContent).toBe("hide");
  });
});

describe("useStickToBottom — H4 innerRef ResizeObserver", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("ResizeObserver observes the inner content div (not the scroll container)", () => {
    let capturedEl: Element | null = null;

    // Must use a class (not arrow fn) so `new ResizeObserver(cb)` works
    class MockRO {
      constructor(_cb: ResizeObserverCallback) {}
      observe(el: Element) { capturedEl = el; }
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", MockRO);

    render(React.createElement(TestHarnessWithInner));
    const inner = screen.getByTestId("inner");

    // The hook must observe the inner div, not the outer container
    expect(capturedEl).toBe(inner);
  });

  it("auto-scrolls the container when at-bottom and inner content grows (H4)", () => {
    let roCallback: (() => void) | null = null;

    class MockRO {
      constructor(cb: () => void) { roCallback = cb; }
      observe(_el: Element) {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", MockRO);

    render(React.createElement(TestHarnessWithInner));
    const container = screen.getByTestId("container");

    // Simulate at-bottom: distFromBottom = 0
    const { getScrollTop } = mockScrollProps(container, {
      scrollTop: 900,
      scrollHeight: 1000,
      clientHeight: 100,
    });

    // Fire scroll so isStuck.current becomes true (at bottom)
    fireEvent.scroll(container);

    // Simulate inner content growing (streaming tokens arrive)
    act(() => { roCallback?.(); });

    // Should have scrolled to bottom (scrollHeight - clientHeight = 900)
    expect(getScrollTop()).toBe(900);
  });

  it("does NOT auto-scroll when user has scrolled up and content grows (H4)", () => {
    let roCallback: (() => void) | null = null;

    class MockRO {
      constructor(cb: () => void) { roCallback = cb; }
      observe(_el: Element) {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", MockRO);

    render(React.createElement(TestHarnessWithInner));
    const container = screen.getByTestId("container");

    // Simulate scrolled up: distFromBottom >> STICK_THRESHOLD
    mockScrollProps(container, { scrollTop: 0, scrollHeight: 1000, clientHeight: 100 });
    fireEvent.scroll(container); // isStuck.current → false

    // Simulate inner content growing
    act(() => { roCallback?.(); });

    // scrollTop should remain 0 (no auto-scroll when not stuck)
    expect(container.scrollTop).toBe(0);
  });
});
