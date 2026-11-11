"use client";

import { AlertCircle, Inbox, Lock, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError } from "@/lib/api/errors";

/** A failed request: what happened, the request id (for support and the audit log) and a retry. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (isApiError(error, 404)) return <NotFound />;
  if (isApiError(error, 403)) return <NotAvailable />;
  const requestId = isApiError(error) ? error.requestId : null;
  const message = isApiError(error) ? error.message : "The server could not be reached.";
  return (
    <Alert variant="destructive" className="max-w-2xl">
      <AlertCircle aria-hidden />
      <AlertTitle>Something went wrong</AlertTitle>
      <AlertDescription>
        <p>{message}</p>
        {requestId && (
          <p className="text-xs">
            Request id: <code className="font-mono select-all">{requestId}</code>
          </p>
        )}
        {onRetry && (
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
            Try again
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}

function Message({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed bg-card px-6 py-12 text-center">
      <span aria-hidden className="text-muted-foreground">{icon}</span>
      <h2 className="text-base font-semibold">{title}</h2>
      {children && <div className="max-w-md text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return <Message icon={<Inbox className="size-8" />} title={title}>{children}</Message>;
}

/** Same message for "does not exist" and "belongs to another center" (the API cannot tell). */
export function NotFound() {
  return (
    <Message icon={<SearchX className="size-8" />} title="Not found">
      There is no such record in your center.
    </Message>
  );
}

export function NotAvailable() {
  return (
    <Message icon={<Lock className="size-8" />} title="Not available for your role">
      Your role does not give access to this page.
    </Message>
  );
}

/** Placeholder while a page's first data loads. */
export function PageSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
