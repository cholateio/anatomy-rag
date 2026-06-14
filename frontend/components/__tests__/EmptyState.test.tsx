import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { EmptyState } from "@/components/EmptyState";

describe("EmptyState", () => {
  it("renders example question buttons", () => {
    render(<EmptyState onPick={vi.fn()} />);
    expect(screen.getByText(/臂叢神經/)).toBeInTheDocument();
    expect(screen.getByText(/冠狀動脈/)).toBeInTheDocument();
    expect(screen.getByText(/腹腔神經叢/)).toBeInTheDocument();
  });

  it("calls onPick with the first question when clicked", () => {
    const onPick = vi.fn();
    render(<EmptyState onPick={onPick} />);
    fireEvent.click(screen.getByText(/臂叢神經/));
    expect(onPick).toHaveBeenCalledWith("臂叢神經的組成與分布為何？");
  });

  it("calls onPick with the second question when clicked", () => {
    const onPick = vi.fn();
    render(<EmptyState onPick={onPick} />);
    fireEvent.click(screen.getByText(/冠狀動脈/));
    expect(onPick).toHaveBeenCalledWith("心臟的冠狀動脈如何供血至各區域？");
  });

  it("calls onPick with the third question when clicked", () => {
    const onPick = vi.fn();
    render(<EmptyState onPick={onPick} />);
    fireEvent.click(screen.getByText(/腹腔神經叢/));
    expect(onPick).toHaveBeenCalledWith("腹腔神經叢的位置與功能為何？");
  });
});
