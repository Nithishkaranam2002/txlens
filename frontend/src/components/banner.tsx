import { ShieldAlert } from "lucide-react";

export const BANNER_TEXT = "Synthetic data. Clinical decision support only, not diagnosis.";

/** Always-visible notice: no real patients, and the app never diagnoses. */
export function SafetyBanner() {
  return (
    <div
      role="note"
      aria-label="Data and use notice"
      className="flex items-center justify-center gap-2 bg-severity-medium-bg px-4 py-1.5 text-sm font-medium text-severity-medium"
    >
      <ShieldAlert aria-hidden className="size-4 shrink-0" />
      <span>{BANNER_TEXT}</span>
    </div>
  );
}
