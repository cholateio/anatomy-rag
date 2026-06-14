import { CitationPanel } from "@/components/CitationPanel";
import { UnverifiedBanner } from "@/components/UnverifiedBanner";
import { FeedbackButtons } from "@/components/FeedbackButtons";
import { Watermark } from "@/components/Watermark";
import { cn } from "@/lib/utils";
import type { AnatomyUIMessage, SourcesData, VerificationData } from "@/lib/types";

type Status = "submitted" | "streaming" | "ready" | "error";

interface MessageBubbleProps {
  message: AnatomyUIMessage;
  status: Status;
  /**
   * M7 — only the last assistant message in a streaming response should show
   * the streaming cursor.  MessageList computes this per-bubble and passes it
   * explicitly so that earlier messages are never wrongly decorated.
   */
  isStreaming?: boolean;
}

/**
 * Renders a single chat turn.
 *
 * **assistant** — vertical stack: answer text → CitationPanel → UnverifiedBanner
 *                 → (FeedbackButtons | Watermark) row.  Streaming cursor appended
 *                 to text when isStreaming === true.
 * **user**       — plain text bubble, right-aligned; no citations / feedback / watermark.
 *
 * Parts are extracted from `message.parts` per the AI SDK UI-message-stream protocol:
 *   • `{ type: "text",             text: string }`
 *   • `{ type: "data-sources",     data: SourcesData }`
 *   • `{ type: "data-verification",data: VerificationData }`
 */
export function MessageBubble({ message, status, isStreaming }: MessageBubbleProps) {
  if (message.role === "user") {
    return <UserBubble message={message} />;
  }
  return <AssistantBubble message={message} status={status} isStreaming={isStreaming} />;
}

// ─── User bubble ────────────────────────────────────────────────────────────

function UserBubble({ message }: { message: AnatomyUIMessage }) {
  const text = extractText(message);

  return (
    <div className="flex justify-end px-4 py-1 sm:px-6">
      <div
        className={cn(
          "max-w-[80%] rounded-2xl rounded-br-sm px-4 py-3",
          "bg-anatomy-accent-subtle border border-anatomy-accent/20",
          "text-sm leading-relaxed text-foreground",
        )}
      >
        {text}
      </div>
    </div>
  );
}

// ─── Assistant bubble ────────────────────────────────────────────────────────

function AssistantBubble({
  message,
  status: _status,
  isStreaming,
}: {
  message: AnatomyUIMessage;
  status: Status;
  isStreaming?: boolean;
}) {
  const text = extractText(message);
  const sourcesData = extractDataPart<SourcesData>(message, "data-sources");
  const verificationData = extractDataPart<VerificationData>(message, "data-verification");

  // M7: use explicit isStreaming prop for cursor; never infer from global status
  // so that earlier messages in a multi-turn conversation stay cursor-free.
  const showCursor = isStreaming === true;

  return (
    <div
      className={cn(
        "group flex flex-col px-4 py-2 sm:px-6",
        "border-b border-border/30 last:border-b-0",
      )}
    >
      {/* Answer text ─────────────────────────────────────────────────────── */}
      <div
        className={cn(
          "mb-4 text-sm leading-relaxed text-foreground",
          // Left accent bar — like a blockquote in an atlas
          "border-l-2 border-anatomy-accent/30 pl-3",
        )}
      >
        {text}
        {/* Streaming cursor — visual only, hidden from AT */}
        {showCursor && (
          <span
            data-testid="streaming-cursor"
            aria-hidden="true"
            className="ml-0.5 inline-block animate-pulse text-anatomy-accent select-none"
          >
            ▌
          </span>
        )}
      </div>

      {/* Citation panel — §6.7: ALWAYS rendered; shows empty-state when no sources ── */}
      <div className="mb-3">
        <CitationPanel data={sourcesData ?? { sources: [] }} />
      </div>

      {/* Unverified banner ────────────────────────────────────────────────── */}
      {verificationData && (
        <div className="mb-3">
          <UnverifiedBanner data={verificationData} />
        </div>
      )}

      {/* Feedback + Watermark row ─────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-4 pt-1">
        <FeedbackButtons messageId={message.id} />
        <Watermark className="text-right" />
      </div>
    </div>
  );
}

// ─── Part extraction helpers ─────────────────────────────────────────────────

/** Concatenate all `text` parts into a single string. */
function extractText(message: AnatomyUIMessage): string {
  return message.parts
    .filter((p) => p.type === "text")
    .map((p) => (p as { type: "text"; text: string }).text)
    .join("");
}

/** Find the first data part with the given type and return its `.data`. */
function extractDataPart<T>(message: AnatomyUIMessage, type: string): T | undefined {
  const part = message.parts.find((p) => p.type === type);
  if (!part) return undefined;
  return (part as { type: string; data: T }).data;
}
