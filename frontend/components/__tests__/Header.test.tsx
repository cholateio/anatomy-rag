import { render, screen } from "@testing-library/react";
import { Header } from "@/components/Header";

describe("Header", () => {
  it("renders the app title", () => {
    render(<Header />);
    expect(screen.getByText(/解剖學 RAG/)).toBeInTheDocument();
  });

  it("renders the educational badge", () => {
    render(<Header />);
    expect(screen.getByText(/教育用途/)).toBeInTheDocument();
  });
});
