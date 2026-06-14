import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { MessageList } from "@/components/MessageList";
import type { AnatomyUIMessage } from "@/lib/types";

// Suppress sub-component rendering noise in unit tests.
// Captures isStreaming so M7 tests can assert per-bubble streaming prop.
vi.mock("@/components/MessageBubble", () => ({
  MessageBubble: ({
    message,
    isStreaming,
  }: {
    message: AnatomyUIMessage;
    isStreaming?: boolean;
  }) =>
    React.createElement("div", {
      "data-testid": "bubble",
      "data-msg-id": message.id,
      "data-streaming": String(isStreaming ?? false),
    }, message.role),
}));

vi.mock("@/lib/api", () => ({
  postFeedback: vi.fn().mockResolvedValue(undefined),
}));

import React from "react";

describe("MessageList", () => {
  it("renders EmptyState when there are no messages", () => {
    const onPickExample = vi.fn();
    render(<MessageList messages={[]} status="ready" onPickExample={onPickExample} />);
    // EmptyState renders the example question buttons
    expect(screen.getByText(/臂叢神經/)).toBeInTheDocument();
  });

  it("clicking an example question in EmptyState calls onPickExample", () => {
    const onPickExample = vi.fn();
    render(<MessageList messages={[]} status="ready" onPickExample={onPickExample} />);
    fireEvent.click(screen.getByText(/臂叢神經/));
    expect(onPickExample).toHaveBeenCalledWith("臂叢神經的組成與分布為何？");
  });

  it("renders MessageBubble for each message when messages are present", () => {
    const messages: AnatomyUIMessage[] = [
      {
        id: "u1",
        role: "user",
        parts: [{ type: "text" as never, text: "Q" as never }],
        metadata: undefined as never,
      } as AnatomyUIMessage,
      {
        id: "a1",
        role: "assistant",
        parts: [{ type: "text" as never, text: "A" as never }],
        metadata: undefined as never,
      } as AnatomyUIMessage,
    ];
    const onPickExample = vi.fn();
    render(<MessageList messages={messages} status="ready" onPickExample={onPickExample} />);
    const bubbles = screen.getAllByTestId("bubble");
    expect(bubbles).toHaveLength(2);
  });

  it("only the last assistant bubble gets isStreaming=true when status=streaming (M7)", () => {
    const messages: AnatomyUIMessage[] = [
      {
        id: "a1",
        role: "assistant",
        parts: [{ type: "text" as never, text: "A1" as never }],
        metadata: undefined as never,
      } as AnatomyUIMessage,
      {
        id: "u1",
        role: "user",
        parts: [{ type: "text" as never, text: "Q" as never }],
        metadata: undefined as never,
      } as AnatomyUIMessage,
      {
        id: "a2",
        role: "assistant",
        parts: [{ type: "text" as never, text: "A2" as never }],
        metadata: undefined as never,
      } as AnatomyUIMessage,
    ];
    render(<MessageList messages={messages} status="streaming" onPickExample={vi.fn()} />);
    const bubbles = screen.getAllByTestId("bubble");
    // First assistant (idx=0): NOT the last assistant → isStreaming=false
    expect(bubbles[0].dataset.streaming).toBe("false");
    // User (idx=1): never streaming
    expect(bubbles[1].dataset.streaming).toBe("false");
    // Second (last) assistant (idx=2): isStreaming=true
    expect(bubbles[2].dataset.streaming).toBe("true");
  });

  it("does NOT render EmptyState when messages are present", () => {
    const messages: AnatomyUIMessage[] = [
      {
        id: "u1",
        role: "user",
        parts: [{ type: "text" as never, text: "Q" as never }],
        metadata: undefined as never,
      } as AnatomyUIMessage,
    ];
    render(<MessageList messages={messages} status="ready" onPickExample={vi.fn()} />);
    expect(screen.queryByText(/臂叢神經/)).not.toBeInTheDocument();
  });
});
