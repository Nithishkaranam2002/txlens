"use client";

import { HelpCircle } from "lucide-react";
import type { ReactElement, ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * A plain-English label with the internal code behind it on hover and on keyboard focus.
 * `children` must be one focusable element (a link) or plain text (then wrapped in a
 * focusable span).
 */
export function CodeTooltip({ code, children }: { code: string | null | undefined; children: ReactElement | string }) {
  if (!code) return <>{children}</>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {typeof children === "string" ? (
          <span tabIndex={0} className="cursor-help underline decoration-dotted underline-offset-4">{children}</span>
        ) : (
          children
        )}
      </TooltipTrigger>
      <TooltipContent>
        Code: <span className="font-mono">{code}</span>
      </TooltipContent>
    </Tooltip>
  );
}

/** A small "?" button that explains something on hover and on keyboard focus. */
export function HelpTooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" aria-label={label} className="ml-1 inline-flex align-middle text-muted-foreground hover:text-foreground">
          <HelpCircle aria-hidden className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent>{children}</TooltipContent>
    </Tooltip>
  );
}
