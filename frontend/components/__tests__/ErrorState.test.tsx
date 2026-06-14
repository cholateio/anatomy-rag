import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { ErrorState } from "@/components/ErrorState";

describe("ErrorState", () => {
  it("renders a friendly Chinese error message", () => {
    render(<ErrorState onRetry={vi.fn()} />);
    expect(screen.getByText(/無法取得回答/)).toBeInTheDocument();
  });

  it("calls onRetry when 重試 is clicked", () => {
    const onRetry = vi.fn();
    render(<ErrorState onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "重試" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not expose internal error details", () => {
    const error = new Error("Internal: DB connection refused at :5432");
    render(<ErrorState error={error} onRetry={vi.fn()} />);
    // Should NOT render the raw error message
    expect(screen.queryByText(/DB connection/)).not.toBeInTheDocument();
    expect(screen.queryByText(/5432/)).not.toBeInTheDocument();
  });
});
