"use client";

import { useMemo } from "react";
import { useChat } from "@ai-sdk/react";
import { DisclaimerModal } from "@/components/DisclaimerModal";
import { Header } from "@/components/Header";
import { MessageList } from "@/components/MessageList";
import { Composer } from "@/components/Composer";
import { ErrorState } from "@/components/ErrorState";
import { makeChatTransport } from "@/lib/transport";
import { getOrCreateConversationId } from "@/lib/conversation";
import type { AnatomyUIMessage } from "@/lib/types";

/**
 * Root chat UI client component.
 *
 * Wires `useChat` (AI SDK v6) to the backend via `DefaultChatTransport` (POST /chat,
 * UI-message-stream protocol).  NO `onData` callback is used — `data-sources` and
 * `data-verification` parts are **persistent** and appear directly in `message.parts`.
 *
 * Layout: DisclaimerModal gate → Header → (flex h-dvh col):
 *   MessageList (flex-1) | ErrorState (if error) | Composer (sticky bottom)
 */
export function ChatPanel() {
  // Create transport exactly once per mount.
  // The typeof-window guard ensures SSR (Node.js) doesn't call sessionStorage.
  const transport = useMemo(
    () =>
      makeChatTransport(
        typeof window !== "undefined" ? getOrCreateConversationId() : "_ssr",
      ),
    [],
  );

  const { messages, sendMessage, status, error, regenerate } =
    useChat<AnatomyUIMessage>({ transport });

  return (
    <>
      {/* Blocking disclaimer gate — auto-dismissed if already accepted */}
      <DisclaimerModal />

      {/* Main shell — full-viewport flex column */}
      <div className="flex h-dvh flex-col">
        <Header />

        {/* Message area (flex-1 + scroll handled inside MessageList) */}
        <MessageList
          messages={messages}
          status={status}
          onPickExample={(q) => void sendMessage({ text: q })}
          className="flex-1"
        />

        {/* Error state — shown above the Composer when streaming failed */}
        {status === "error" && (
          <ErrorState error={error} onRetry={regenerate} />
        )}

        {/* Input composer — sticky bottom */}
        <Composer
          onSend={(t) => void sendMessage({ text: t })}
          status={status}
        />
      </div>
    </>
  );
}
