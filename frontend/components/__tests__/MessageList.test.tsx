import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { MessageList } from "@/components/MessageList";
import type { AnatomyUIMessage } from "@/lib/types";

// Suppress sub-component rendering noise in unit tests
vi.mock("@/components/MessageBubble", () => ({
  MessageBubble: ({ message }: { message: AnatomyUIMessage }) =>
    React.createElement("div", { "data-testid": "bubble", "data-msg-id": message.id }, message.role),
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
