import { render, screen } from "@testing-library/react";
import { Watermark } from "@/components/Watermark";

describe("Watermark", () => {
  it("renders the exact watermark text", () => {
    render(<Watermark />);
    expect(screen.getByText("教育用途，內容基於教科書")).toBeInTheDocument();
  });
});
