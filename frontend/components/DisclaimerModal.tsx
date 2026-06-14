"use client";

import React, { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  isDisclaimerAccepted,
  acceptDisclaimer,
} from "@/lib/disclaimer";
import { cn } from "@/lib/utils";

/**
 * 首次啟動免責聲明模態 (§6.7)
 * 阻斷式：未接受前不可操作主介面。
 * 行動版：近全螢幕底部拉起；桌機：置中 Dialog。
 * 使用 useEffect 而非 SSR 初值，避免 hydration mismatch（disclaimer 依賴 localStorage）。
 */
export function DisclaimerModal() {
  const [open, setOpen] = useState(false);

  // 僅在 client 端檢查 localStorage，避免 hydration 不一致
  useEffect(() => {
    if (!isDisclaimerAccepted()) {
      setOpen(true);
    }
  }, []);

  const handleAccept = () => {
    acceptDisclaimer();
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      {/*
       * 行動版底部拉起：覆蓋 DialogContent 的置中定位，改為貼底 + 頂端圓角。
       * 桌機 (sm:) 恢復正常置中 Dialog 樣式。
       */}
      <DialogContent
        showCloseButton={false}
        className={cn(
          // 行動版：貼底、全寬、頂端圓角
          "fixed inset-x-0 bottom-0 top-auto max-w-none translate-x-0 translate-y-0",
          "rounded-t-2xl border-t border-x border-b-0",
          "pb-[env(safe-area-inset-bottom)]",
          // 桌機：恢復置中
          "sm:inset-auto sm:bottom-auto sm:left-[50%] sm:top-[50%]",
          "sm:translate-x-[-50%] sm:translate-y-[-50%]",
          "sm:max-w-md sm:rounded-xl sm:border",
        )}
        // 阻止點擊外部關閉（必須點按鈕）
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader className="text-left">
          <DialogTitle className="font-serif text-lg">
            使用須知
          </DialogTitle>
          <DialogDescription className="sr-only">
            使用本系統前請閱讀並同意以下說明
          </DialogDescription>
        </DialogHeader>

        {/* 三項說明條款 */}
        <ul className="space-y-3 py-1 text-sm leading-relaxed text-muted-foreground">
          <li className="flex gap-2.5">
            <span
              className="mt-0.5 shrink-0 font-mono text-xs text-anatomy-accent"
              aria-hidden="true"
            >
              01
            </span>
            <span>
              <strong className="font-medium text-foreground">教育用途：</strong>
              本系統僅供解剖學教學輔助使用，不可作為臨床診斷或治療依據。
            </span>
          </li>
          <li className="flex gap-2.5">
            <span
              className="mt-0.5 shrink-0 font-mono text-xs text-anatomy-accent"
              aria-hidden="true"
            >
              02
            </span>
            <span>
              <strong className="font-medium text-foreground">自行驗證：</strong>
              系統可能出錯，應自行核對原始教科書內容，勿直接引用。
            </span>
          </li>
          <li className="flex gap-2.5">
            <span
              className="mt-0.5 shrink-0 font-mono text-xs text-anatomy-accent"
              aria-hidden="true"
            >
              03
            </span>
            <span>
              <strong className="font-medium text-foreground">查詢記錄：</strong>
              查詢日誌會儲存供品質改善用途，不含可識別個人資料。
            </span>
          </li>
        </ul>

        <DialogFooter className="pt-1">
          <Button
            onClick={handleAccept}
            className={cn(
              "w-full min-h-11",
              "bg-anatomy-accent text-anatomy-accent-fg",
              "hover:bg-anatomy-accent/90",
              "sm:w-auto",
            )}
          >
            我了解並同意
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
