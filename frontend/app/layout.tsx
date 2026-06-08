import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "解剖學 RAG 問答系統",
  description: "以解剖學教科書為基礎的多模態 RAG 問答系統（醫學系內部使用）",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
