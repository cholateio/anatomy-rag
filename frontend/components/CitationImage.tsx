"use client";

import React, { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface CitationImageProps {
  src: string;
  alt: string;
  className?: string;
}

/**
 * 引文縮圖 + 全螢幕燈箱
 * 載入失敗時顯示占位圖（解剖頁面示意）；點擊後全螢幕展開以便辨識標籤。
 */
export function CitationImage({ src, alt, className }: CitationImageProps) {
  const [imgSrc, setImgSrc] = useState(src);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  return (
    <>
      {/* 縮圖 — 點擊開啟燈箱；觸控目標 ≥44px */}
      <button
        type="button"
        aria-label={`放大圖片：${alt}`}
        className={cn(
          "group relative block min-h-11 w-full overflow-hidden rounded-md border border-border",
          "bg-muted/40 transition-opacity hover:opacity-90 focus-visible:outline-none",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          className,
        )}
        onClick={() => setLightboxOpen(true)}
      >
        <img
          src={imgSrc}
          alt={alt}
          loading="lazy"
          onError={() => setImgSrc("/placeholder-page.svg")}
          className="h-auto w-full object-contain"
        />
      </button>

      {/* 燈箱 Dialog — 全螢幕顯示以便識讀解剖標籤 */}
      <Dialog open={lightboxOpen} onOpenChange={setLightboxOpen}>
        <DialogContent
          className={cn(
            "max-w-none! w-screen! h-screen! max-h-none! translate-x-[-50%] translate-y-[-50%]",
            "flex flex-col items-center justify-center p-4 sm:p-8",
            "bg-background/98 backdrop-blur-sm",
          )}
          showCloseButton
        >
          <DialogTitle className="sr-only">{alt}</DialogTitle>
          <DialogDescription className="sr-only">
            點擊關閉以回到閱讀
          </DialogDescription>
          <img
            src={imgSrc}
            alt={alt}
            className="max-h-full max-w-full object-contain"
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
