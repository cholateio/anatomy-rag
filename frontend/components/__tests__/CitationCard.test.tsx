import { render, screen } from "@testing-library/react";
import { CitationCard } from "@/components/CitationCard";
import type { Citation } from "@/lib/types";

const grayCitation: Citation = {
  book_title: "Gray's Anatomy",
  edition: "42nd",
  page: 812,
  figure: "8.12",
  image_url: "/test-page.jpg",
  snippet: "The brachial plexus arises from the ventral rami of C5–T1.",
  score: 0.95,
};

describe("CitationCard", () => {
  it("renders book title and page number", () => {
    render(<CitationCard c={grayCitation} />);
    expect(screen.getByText(/Gray/)).toBeInTheDocument();
    expect(screen.getByText(/812/)).toBeInTheDocument();
  });

  it("renders edition when provided", () => {
    render(<CitationCard c={grayCitation} />);
    expect(screen.getByText(/42nd/)).toBeInTheDocument();
  });

  it("renders figure when provided", () => {
    render(<CitationCard c={grayCitation} />);
    expect(screen.getByText(/8\.12/)).toBeInTheDocument();
  });

  it("renders without figure or edition gracefully", () => {
    const c: Citation = { ...grayCitation, figure: null, edition: null };
    render(<CitationCard c={c} />);
    expect(screen.getByText(/Gray/)).toBeInTheDocument();
  });
});
