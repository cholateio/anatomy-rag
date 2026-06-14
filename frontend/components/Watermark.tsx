import { cn } from "@/lib/utils";

/**
 * 底部教育水印 (§6.7)
 * 每則回答尾端的小型法遵提示，刻意做到靜默但清晰。
 */
export function Watermark({ className }: { className?: string }) {
  return (
    <p
      className={cn(
        "text-[11px] leading-relaxed tracking-wide text-muted-foreground/60 select-none",
        className,
      )}
    >
      教育用途，內容基於教科書
    </p>
  );
}
