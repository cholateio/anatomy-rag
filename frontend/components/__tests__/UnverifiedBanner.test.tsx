import { render, screen } from "@testing-library/react";
import { UnverifiedBanner } from "@/components/UnverifiedBanner";
import type { VerificationData } from "@/lib/types";

describe("UnverifiedBanner", () => {
  it("shows alert with 未驗證 and unverified snippets when not verified", () => {
    const data: VerificationData = {
      verified: false,
      has_citations: true,
      unverified: ["[X, p.1]"],
    };
    render(<UnverifiedBanner data={data} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alert").textContent).toMatch(/未驗證/);
    expect(screen.getByText("[X, p.1]")).toBeInTheDocument();
  });

  it("renders nothing when verified=true", () => {
    const data: VerificationData = {
      verified: true,
      has_citations: true,
      unverified: ["[X, p.1]"],
    };
    const { container } = render(<UnverifiedBanner data={data} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when has_citations=false", () => {
    const data: VerificationData = {
      verified: false,
      has_citations: false,
      unverified: [],
    };
    const { container } = render(<UnverifiedBanner data={data} />);
    expect(container).toBeEmptyDOMElement();
  });
});
