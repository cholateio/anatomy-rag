import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";

// Mock @/lib/api before importing the component
vi.mock("@/lib/api", () => ({
  postFeedback: vi.fn().mockResolvedValue(undefined),
}));

import { FeedbackButtons } from "@/components/FeedbackButtons";
import { postFeedback } from "@/lib/api";

const FIXED_TURN = "00000000-0000-0000-0000-0000000000aa";

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("FeedbackButtons", () => {
  it("shows hint text and textbox after clicking 👎 (first time)", () => {
    render(<FeedbackButtons messageId={FIXED_TURN} />);
    fireEvent.click(screen.getByTitle("需要改善"));
    // First-downvote hint should appear
    expect(screen.getByText(/回報/)).toBeInTheDocument();
    // Text box should appear
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("calls postFeedback with rating:-1 and text after filling and submitting", async () => {
    render(<FeedbackButtons messageId={FIXED_TURN} />);
    fireEvent.click(screen.getByTitle("需要改善"));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "頁碼錯" },
    });
    fireEvent.click(screen.getByText("送出"));
    await waitFor(() => {
      expect(postFeedback).toHaveBeenCalledWith({
        messageId: FIXED_TURN,
        rating: -1,
        text: "頁碼錯",
      });
    });
  });

  it("calls postFeedback with rating:1 after clicking 👍", async () => {
    render(<FeedbackButtons messageId={FIXED_TURN} />);
    fireEvent.click(screen.getByTitle("有幫助"));
    await waitFor(() => {
      expect(postFeedback).toHaveBeenCalledWith({
        messageId: FIXED_TURN,
        rating: 1,
      });
    });
  });

  it("shows 已收到回饋 after submitting and prevents duplicate submit", async () => {
    render(<FeedbackButtons messageId={FIXED_TURN} />);
    fireEvent.click(screen.getByTitle("有幫助"));
    await waitFor(() => {
      expect(screen.getByText("已收到回饋")).toBeInTheDocument();
    });
    // Buttons no longer present
    expect(screen.queryByTitle("有幫助")).not.toBeInTheDocument();
  });

  it("does NOT show first-downvote hint on second downvote (after localStorage cleared but hint already marked)", () => {
    // First render: triggers hint and marks it
    const { unmount } = render(<FeedbackButtons messageId={FIXED_TURN} />);
    fireEvent.click(screen.getByTitle("需要改善"));
    unmount();

    // Second render (hint was marked, but we clear localStorage so hint resets)
    // Actually test: once markFirstDownvotePrompted was called, hint won't appear again
    // We manually set the key to simulate second time
    localStorage.setItem("anatomy-rag:first-downvote", "1");
    render(<FeedbackButtons messageId={FIXED_TURN} />);
    fireEvent.click(screen.getByTitle("需要改善"));
    // Hint paragraph should NOT appear (the specific one about 品質改善)
    expect(screen.queryByText(/品質改善/)).not.toBeInTheDocument();
  });
});
