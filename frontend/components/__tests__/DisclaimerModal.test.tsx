import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import { DisclaimerModal } from "@/components/DisclaimerModal";

beforeEach(() => {
  localStorage.clear();
});

describe("DisclaimerModal", () => {
  it("shows modal with disclaimer content when not accepted", async () => {
    render(<DisclaimerModal />);
    // useEffect runs after render; waitFor ensures effect has flushed
    await waitFor(() => {
      expect(screen.getByText(/系統可能出錯/)).toBeInTheDocument();
    });
  });

  it("shows all three disclaimer points", async () => {
    render(<DisclaimerModal />);
    await waitFor(() => {
      expect(screen.getByText(/教育用途/)).toBeInTheDocument();
      expect(screen.getByText(/系統可能出錯/)).toBeInTheDocument();
      expect(screen.getByText(/查詢日誌/)).toBeInTheDocument();
    });
  });

  it("hides modal after accepting and persists acceptance", async () => {
    const { rerender } = render(<DisclaimerModal />);
    await waitFor(() => {
      expect(screen.getByText(/系統可能出錯/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/我了解並同意/));

    rerender(<DisclaimerModal />);
    // After clicking agree and rerendering, modal content should be gone
    await waitFor(() => {
      expect(screen.queryByText(/系統可能出錯/)).not.toBeInTheDocument();
    });
  });

  it("does not show modal when already accepted", async () => {
    localStorage.setItem("anatomy-rag:disclaimer:v1", "1");
    const { container } = render(<DisclaimerModal />);
    // Give effects time to run
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/系統可能出錯/)).not.toBeInTheDocument();
  });
});
