import { AlertOctagon, AlertTriangle, Info } from "lucide-react";

import type { FlagStatus, Severity } from "@/lib/types";
import { cn } from "@/lib/utils";

const SEVERITY = {
  high: { label: "High", icon: AlertOctagon, className: "bg-severity-high-bg text-severity-high border-severity-high/30" },
  medium: { label: "Medium", icon: AlertTriangle, className: "bg-severity-medium-bg text-severity-medium border-severity-medium/30" },
  low: { label: "Low", icon: Info, className: "bg-severity-low-bg text-severity-low border-severity-low/30" },
} as const;

/** Severity as text plus icon (never colour alone). It ranks review urgency; it is not a diagnosis. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const { label, icon: Icon, className } = SEVERITY[severity];
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-semibold",
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      {label}
      <span className="sr-only"> severity</span>
    </span>
  );
}

const STATUS_TEXT: Record<FlagStatus, string> = { new: "New", acknowledged: "Acknowledged", resolved: "Resolved" };

export function StatusBadge({ status, cleared = false }: { status: FlagStatus; cleared?: boolean }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-md border bg-card px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
      {cleared && status !== "resolved" ? "Cleared" : STATUS_TEXT[status]}
    </span>
  );
}
