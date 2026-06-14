import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect } from "vitest";
import { Composer } from "@/components/Composer";

describe("Composer", () => {
  it("calls onSend with trimmed text when 送出 button is clicked", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} status="ready" />);
    const textarea = screen.getByRole("textbox");
    await userEvent.type(textarea, "臂叢神經的分布");
    fireEvent.click(screen.getByRole("button", { name: "送出" }));
    expect(onSend).toHaveBeenCalledWith("臂叢神經的分布");
  });

  it("clears the textarea after sending", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} status="ready" />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    await userEvent.type(textarea, "test");
    fireEvent.click(screen.getByRole("button", { name: "送出" }));
    expect(textarea.value).toBe("");
  });

  it("disables 送出 button when status=streaming", () => {
    render(<Composer onSend={vi.fn()} status="streaming" />);
    expect(screen.getByRole("button", { name: "送出" })).toBeDisabled();
  });

  it("disables 送出 button when status=submitted", () => {
    render(<Composer onSend={vi.fn()} status="submitted" />);
    expect(screen.getByRole("button", { name: "送出" })).toBeDisabled();
  });

  it("does not call onSend with empty or whitespace-only text", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} status="ready" />);
    fireEvent.click(screen.getByRole("button", { name: "送出" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("submits on Enter key (no Shift)", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} status="ready" />);
    const textarea = screen.getByRole("textbox");
    await userEvent.type(textarea, "心臟解剖");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledWith("心臟解剖");
  });

  it("does NOT submit on Shift+Enter", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} status="ready" />);
    const textarea = screen.getByRole("textbox");
    await userEvent.type(textarea, "some text");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });
});
