import type { UIMessage } from "ai";

export type Citation = {
  book_title: string;
  edition?: string | null;
  page: number;
  figure?: string | null;
  image_url: string;
  snippet: string;
  score: number;
};
export type SourcesData = { sources: Citation[] };
export type VerificationData = { verified: boolean; has_citations: boolean; unverified: string[] };

/** 後端 data-sources/data-verification 為 persistent → 進 message.parts。 */
export type AnatomyUIMessage = UIMessage<never, { sources: SourcesData; verification: VerificationData }>;
