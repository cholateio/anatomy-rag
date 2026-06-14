import { AlertCircleIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  error?: Error;
  onRetry: () => void;
  className?: string;
}

/**
 * 錯誤狀態提示
 * 不洩漏技術細節；提供友善的繁中說明與重試按鈕。
 */
export function ErrorState({ onRetry, className }: ErrorStateProps) {
  return (
    <div
      className={cn(
        "mx-auto flex max-w-md flex-col items-center px-4 py-12 text-center",
        className,
      )}
      role="alert"
    >
      <div
        className={cn(
          "mb-4 flex size-14 items-center justify-center rounded-full",
          "bg-destructive/10 text-destructive",
        )}
        aria-hidden="true"
      >
        <AlertCircleIcon className="size-7" />
      </div>

      <h2 className="mb-2 font-serif text-lg font-semibold text-foreground">
        暫時無法取得回答
      </h2>
      <p className="mb-6 text-sm leading-relaxed text-muted-foreground">
        系統發生問題，請稍後再試。
        <br />
        若問題持續，請聯絡系統管理員。
      </p>

      <Button
        variant="outline"
        onClick={onRetry}
        className={cn(
          "min-h-11 min-w-[88px]",
          "border-border hover:border-anatomy-accent/40",
          "hover:bg-anatomy-accent-subtle hover:text-anatomy-accent",
        )}
      >
        重試
      </Button>
    </div>
  );
}
