"use client";

import { ErrorState } from "@/components/states";

/** A render error in a signed-in page. Nothing is logged: errors can carry patient data. */
export default function SignedInError({ error, retry }: { error: unknown; retry: () => void }) {
  return <ErrorState error={error} onRetry={retry} />;
}
