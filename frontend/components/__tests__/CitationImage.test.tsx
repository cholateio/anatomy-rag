import { render, screen, fireEvent } from "@testing-library/react";
import { CitationImage } from "@/components/CitationImage";

describe("CitationImage", () => {
  it("renders an img with the given src and alt", () => {
    render(<CitationImage src="/test.jpg" alt="測試圖" />);
    const img = screen.getByRole("img", { name: "測試圖" });
    expect(img).toBeInTheDocument();
  });

  it("swaps to placeholder on error", () => {
    render(<CitationImage src="/test.jpg" alt="測試圖" />);
    const img = screen.getByRole("img", { name: "測試圖" });
    fireEvent.error(img);
    expect(img.getAttribute("src")).toMatch(/placeholder/);
  });

  it("opens a dialog when the image is clicked", () => {
    render(<CitationImage src="/test.jpg" alt="測試圖" />);
    const img = screen.getByRole("img", { name: "測試圖" });
    fireEvent.click(img);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
