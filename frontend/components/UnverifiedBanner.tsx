import { AlertTriangleIcon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import type { VerificationData } from "@/lib/types";

interface UnverifiedBannerProps {
  data: VerificationData;
  className?: string;
}

/**
 * 引文未驗證提示 (D-N)
 * 僅在 verified=false 且確實有引文時顯示；verified=true 或無引文則靜默。
 */
export function UnverifiedBanner({ data, className }: UnverifiedBannerProps) {
  if (data.verified || !data.has_citations) return null;

  return (
    <Alert
      className={cn(
        "border-amber-500/30 bg-amber-50/60 text-amber-900",
        "dark:border-amber-500/20 dark:bg-amber-950/20 dark:text-amber-200",
        className,
      )}
    >
      <AlertTriangleIcon className="size-4 text-amber-600 dark:text-amber-400" />
      <AlertTitle className="text-sm font-medium">
        部分引文未驗證
      </AlertTitle>
      <AlertDescription>
        <p className="mb-1.5 text-xs leading-relaxed">
          以下引文標記未能在教科書頁面中核實，請自行核查：
        </p>
        <ul className="space-y-0.5">
          {data.unverified.map((snippet, i) => (
            <li
              key={i}
              className="font-mono text-[11px] text-amber-800 dark:text-amber-300"
            >
              {snippet}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
