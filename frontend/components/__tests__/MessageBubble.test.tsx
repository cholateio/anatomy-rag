import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { MessageBubble } from "@/components/MessageBubble";
import type { AnatomyUIMessage } from "@/lib/types";
import type { SourcesData } from "@/lib/types";

// Silence CitationImage network warnings in tests
vi.mock("@/components/CitationImage", () => ({
  CitationImage: () => null,
}));

// Mock postFeedback so FeedbackButtons don't make real requests
vi.mock("@/lib/api", () => ({
  postFeedback: vi.fn().mockResolvedValue(undefined),
}));

const sources: SourcesData = {
  sources: [
    {
      book_title: "Gray",
      edition: "42nd",
      page: 812,
      figure: "Fig.7-23",
      image_url: "/p/1.webp",
      snippet: "肱二頭肌起於喙突",
      score: 0.9,
    },
  ],
};

/**
 * Build a mock AnatomyUIMessage whose parts include data-sources, a text part,
 * and data-verification in the order the backend sends them.
 */
function makeAssistantMsg(overrides?: {
  verificationData?: { verified: boolean; has_citations: boolean; unverified: string[] };
  hasSources?: boolean;
}): AnatomyUIMessage {
  const { verificationData, hasSources = true } = overrides ?? {};
  return {
    id: "00000000-0000-0000-0000-000000000001",
    role: "assistant",
    parts: [
      ...(hasSources
        ? [{ type: "data-sources" as never, data: sources as never }]
        : []),
      {
        type: "text" as never,
        text: "起於喙突 [Gray, p.812, Fig.7-23]。" as never,
      },
      {
        type: "data-verification" as never,
        data: (verificationData ?? {
          verified: false,
          has_citations: true,
          unverified: ["[X, p.1]"],
        }) as never,
      },
    ],
    metadata: undefined as never,
  } as AnatomyUIMessage;
}

function makeUserMsg(): AnatomyUIMessage {
  return {
    id: "00000000-0000-0000-0000-000000000002",
    role: "user",
    parts: [{ type: "text" as never, text: "什麼是肱二頭肌？" as never }],
    metadata: undefined as never,
  } as AnatomyUIMessage;
}

describe("MessageBubble — assistant", () => {
  it("renders answer text, citation panel header, unverified banner, watermark", () => {
    render(<MessageBubble message={makeAssistantMsg()} status="ready" />);
    // Answer text (may appear in both the bubble text and the citation snippet)
    expect(screen.getAllByText(/起於喙突/).length).toBeGreaterThan(0);
    // Citation panel header (CitationPanel checks sources.length > 0)
    expect(screen.getByText(/引用/)).toBeInTheDocument();
    // Unverified banner title
    expect(screen.getByText(/未驗證/)).toBeInTheDocument();
    // Watermark
    expect(screen.getByText(/教育用途，內容基於教科書/)).toBeInTheDocument();
  });

  it("does NOT show unverified banner when has_citations=false", () => {
    render(
      <MessageBubble
        message={makeAssistantMsg({
          verificationData: { verified: false, has_citations: false, unverified: [] },
        })}
        status="ready"
      />,
    );
    expect(screen.queryByText(/未驗證/)).not.toBeInTheDocument();
  });

  it("does NOT show unverified banner when verified=true", () => {
    render(
      <MessageBubble
        message={makeAssistantMsg({
          verificationData: { verified: true, has_citations: true, unverified: [] },
        })}
        status="ready"
      />,
    );
    expect(screen.queryByText(/未驗證/)).not.toBeInTheDocument();
  });

  it("shows streaming cursor when isStreaming=true (M7 — explicit prop)", () => {
    render(<MessageBubble message={makeAssistantMsg()} status="ready" isStreaming={true} />);
    expect(screen.getByTestId("streaming-cursor")).toBeInTheDocument();
  });

  it("does NOT show streaming cursor when isStreaming=false even if status=streaming (M7)", () => {
    render(<MessageBubble message={makeAssistantMsg()} status="streaming" isStreaming={false} />);
    expect(screen.queryByTestId("streaming-cursor")).not.toBeInTheDocument();
  });

  it("still shows citation empty-state and watermark when no data-sources part (C1)", () => {
    const noSources: AnatomyUIMessage = {
      id: "00000000-0000-0000-0000-000000000099",
      role: "assistant",
      parts: [{ type: "text" as never, text: "some answer" as never }],
      metadata: undefined as never,
    } as AnatomyUIMessage;
    render(<MessageBubble message={noSources} status="ready" />);
    expect(screen.getByText(/本回答未引用教科書頁面/)).toBeInTheDocument();
    expect(screen.getByText(/教育用途，內容基於教科書/)).toBeInTheDocument();
  });
});

describe("MessageBubble — user", () => {
  it("renders the user text but NOT the watermark or citation panel", () => {
    render(<MessageBubble message={makeUserMsg()} status="ready" />);
    expect(screen.getByText(/什麼是肱二頭肌/)).toBeInTheDocument();
    expect(screen.queryByText(/教育用途，內容基於教科書/)).not.toBeInTheDocument();
    expect(screen.queryByText(/引用/)).not.toBeInTheDocument();
  });
});
