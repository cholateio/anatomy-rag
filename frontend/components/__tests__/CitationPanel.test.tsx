import { render, screen } from "@testing-library/react";
import { CitationPanel } from "@/components/CitationPanel";
import type { SourcesData, Citation } from "@/lib/types";

const makeSource = (overrides: Partial<Citation> = {}): Citation => ({
  book_title: "Netter's Atlas",
  edition: "7th",
  page: 100,
  figure: null,
  image_url: "/img.jpg",
  snippet: "Sample anatomy text.",
  score: 0.9,
  ...overrides,
});

describe("CitationPanel", () => {
  it("renders count header and both book titles for 2 sources", () => {
    const data: SourcesData = {
      sources: [
        makeSource({ book_title: "Netter's Atlas", page: 100 }),
        makeSource({ book_title: "Gray's Anatomy", page: 200 }),
      ],
    };
    render(<CitationPanel data={data} />);
    expect(screen.getByText(/引用.*2/)).toBeInTheDocument();
    expect(screen.getByText(/Netter/)).toBeInTheDocument();
    expect(screen.getByText(/Gray/)).toBeInTheDocument();
  });

  it("renders nothing for empty sources", () => {
    const { container } = render(<CitationPanel data={{ sources: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
