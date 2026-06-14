/**
 * ChatPanel integration tests — use the real useChat hook with a mocked fetch.
 *
 * Pin-verify A: assert that the assistant message.id equals the FIXED_TURN
 *               from the stream's "start" event.  If this fails the backend must
 *               include turn_id in data-verification and the controller must
 *               coordinate that change.
 *
 * Pin-verify B: assert that an error stream chunk causes `status==="error"` and
 *               ErrorState renders.
 */
import React from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

import {
  uiMessageStreamResponse,
  errorStreamResponse,
  FIXED_TURN,
} from "@/lib/__tests__/_sseFixture";

// ─── Mocks set up before component import ────────────────────────────────────

// postFeedback: spy so Pin-verify A can check the messageId it receives
vi.mock("@/lib/api", () => ({
  postFeedback: vi.fn().mockResolvedValue(undefined),
}));

// CitationImage triggers Next/Image internals we don't need
vi.mock("@/components/CitationImage", () => ({
  CitationImage: () => null,
}));

// ─── Component import (after mocks) ──────────────────────────────────────────

import { ChatPanel } from "@/components/ChatPanel";
import { postFeedback } from "@/lib/api";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function stubFetch(resp: Response) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(resp));
}

// ─── Suite ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  // Bypass disclaimer modal
  localStorage.setItem("anatomy-rag:disclaimer:v1", "1");
  sessionStorage.clear();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatPanel integration", () => {
  /**
   * L8 — outgoing request carries conversation_id and omits credentials (multi-chunk stream).
   */
  it("[L8] request body has conversation_id and fetch uses credentials:omit", async () => {
    const mockFetch = vi.fn().mockResolvedValue(uiMessageStreamResponse());
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatPanel />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "肱二頭肌" } });
    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    // Wait for stream to complete
    await waitFor(
      () => expect(screen.getByText(/起於喙突/)).toBeInTheDocument(),
      { timeout: 5000 },
    );

    expect(mockFetch).toHaveBeenCalled();
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];

    // conversation_id must be in the JSON body
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toHaveProperty("conversation_id");

    // Credentials must be omitted (C2 transport fix)
    expect(init.credentials).toBe("omit");
  });

  /**
   * Test 1 — happy-path full stream.
   * Sends a question, awaits the SSE response, asserts rendered text + citations + watermark.
   */
  it("renders answer text, citation panel, and watermark after a successful stream", async () => {
    stubFetch(uiMessageStreamResponse());

    render(<ChatPanel />);

    // Type a question and submit
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "肱二頭肌起點" } });
    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    // Wait for the streamed text to appear
    await waitFor(
      () => expect(screen.getByText(/起於喙突/)).toBeInTheDocument(),
      { timeout: 5000 },
    );

    // Citation panel header
    expect(screen.getByText(/引用/)).toBeInTheDocument();

    // Watermark
    expect(screen.getByText(/教育用途，內容基於教科書/)).toBeInTheDocument();
  });

  /**
   * Pin-verify A — assert that the assistant message.id equals start.messageId.
   *
   * Strategy: after the stream, click 👍 on the rendered FeedbackButtons.
   * FeedbackButtons calls postFeedback({ messageId: message.id, rating: 1 }).
   * If message.id === FIXED_TURN, postFeedback will be called with FIXED_TURN.
   */
  it("[Pin-verify A] assistant message.id equals start.messageId from the stream", async () => {
    stubFetch(uiMessageStreamResponse());
    vi.spyOn(await import("@/lib/api"), "postFeedback").mockResolvedValue(undefined);

    render(<ChatPanel />);

    // Submit a question
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Q" } });
    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    // Wait for stream to finish and answer to be rendered
    await waitFor(() => expect(screen.getByText(/起於喙突/)).toBeInTheDocument(), {
      timeout: 5000,
    });

    // Click the 👍 feedback button for the assistant turn
    const upvoteBtn = screen.getByTitle("有幫助");
    await act(async () => {
      fireEvent.click(upvoteBtn);
    });

    await waitFor(() => {
      expect(postFeedback).toHaveBeenCalledWith({
        messageId: FIXED_TURN,
        rating: 1,
      });
    });
  });

  /**
   * Pin-verify B — error stream causes ErrorState to render.
   *
   * If the AI SDK does not surface status==="error" from the error chunk,
   * we report the actual status/error that appears.
   */
  it("[Pin-verify B] error stream surfaces ErrorState with 重試 button", async () => {
    stubFetch(errorStreamResponse());

    render(<ChatPanel />);

    // Submit to trigger the (error) stream
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Q" } });
    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    // Wait for ErrorState to appear (it renders when status === "error").
    // Use getByRole("button") to avoid ambiguity — the Composer also shows "發生錯誤，請重試"
    // as a text paragraph which would collide with getByText(/重試/).
    await waitFor(
      () => expect(screen.getByRole("button", { name: "重試" })).toBeInTheDocument(),
      { timeout: 5000 },
    );

    // Also assert the error heading
    expect(screen.getByText(/暫時無法取得回答/)).toBeInTheDocument();
  });
});
