import { describe, it, expect, beforeEach } from "vitest";
import { isDisclaimerAccepted, acceptDisclaimer, shouldPromptFirstDownvote, markFirstDownvotePrompted } from "@/lib/disclaimer";
beforeEach(() => localStorage.clear());
describe("disclaimer", () => {
  it("defaults to not accepted, persists on accept", () => {
    expect(isDisclaimerAccepted()).toBe(false);
    acceptDisclaimer();
    expect(isDisclaimerAccepted()).toBe(true);
  });
  it("first downvote prompt fires once", () => {
    expect(shouldPromptFirstDownvote()).toBe(true);
    markFirstDownvotePrompted();
    expect(shouldPromptFirstDownvote()).toBe(false);
  });
});
