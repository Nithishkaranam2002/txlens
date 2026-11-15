"use client";

import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { formatUpdated } from "@/lib/format";

const TICK_MS = 30_000;

/** Reload a list (its filters and sort stay, they are in the query key) and say how fresh it is. */
export function RefreshButton({ onRefresh, fetching, updatedAt }: { onRefresh: () => void; fetching: boolean; updatedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="flex items-center gap-2">
      <Button type="button" variant="outline" size="sm" onClick={onRefresh} disabled={fetching} aria-busy={fetching}>
        {fetching ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <RefreshCw aria-hidden className="size-4" />}
        Refresh
      </Button>
      {updatedAt > 0 && (
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {fetching ? "Updating…" : formatUpdated(updatedAt, Math.max(now, updatedAt))}
        </span>
      )}
    </div>
  );
}
