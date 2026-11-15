import { RotateCcw, ShieldAlert } from "lucide-react";

export const BANNER_TEXT = "Synthetic data. Clinical decision support only, not diagnosis.";
export const GUEST_NOTICE = "Demo account – changes you make reset every night.";

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

/** For the public demo's guest accounts: what they change is put back overnight. */
export function GuestNotice() {
  return (
    <div
      role="note"
      aria-label="Demo account notice"
      className="flex items-center justify-center gap-2 border-b bg-muted px-4 py-1 text-sm text-foreground"
    >
      <RotateCcw aria-hidden className="size-4 shrink-0" />
      <span>{GUEST_NOTICE}</span>
    </div>
  );
}
