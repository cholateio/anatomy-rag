import { AlertTriangleIcon, InfoIcon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import type { VerificationData } from "@/lib/types";

interface UnverifiedBannerProps {
  data: VerificationData;
  className?: string;
}

/**
 * 引文驗證提示 (H5 / §6.7)
 *
 * - verified=true              → null (no banner)
 * - !verified && has_citations → amber 警告：列出未驗證引文標記
 * - !verified && !has_citations→ blue 提示：本回答未附可驗證引文，請以教材核對
 *
 * Both non-verified variants render an accessible role="alert" (via shadcn Alert).
 */
export function UnverifiedBanner({ data, className }: UnverifiedBannerProps) {
  if (data.verified) return null;

  if (!data.has_citations) {
    // Softer informational notice — no citations to verify, but flag the gap
    return (
      <Alert
        className={cn(
          "border-blue-400/30 bg-blue-50/50 text-blue-900",
          "dark:border-blue-500/20 dark:bg-blue-950/20 dark:text-blue-200",
          className,
        )}
      >
        <InfoIcon className="size-4 text-blue-500 dark:text-blue-400" />
        <AlertTitle className="text-sm font-medium">
          引文未附
        </AlertTitle>
        <AlertDescription>
          <p className="text-xs leading-relaxed">
            本回答未附可驗證的教科書引文，請以教材核對
          </p>
        </AlertDescription>
      </Alert>
    );
  }

  // !verified && has_citations → warn about specific unverified citation markers
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
