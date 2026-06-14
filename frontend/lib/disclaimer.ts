const DISCLAIMER = "anatomy-rag:disclaimer:v1";
const FIRST_DOWNVOTE = "anatomy-rag:first-downvote";
export const isDisclaimerAccepted = () => localStorage.getItem(DISCLAIMER) === "1";
export const acceptDisclaimer = () => localStorage.setItem(DISCLAIMER, "1");
export const shouldPromptFirstDownvote = () => localStorage.getItem(FIRST_DOWNVOTE) !== "1";
export const markFirstDownvotePrompted = () => localStorage.setItem(FIRST_DOWNVOTE, "1");
