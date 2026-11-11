"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isApiError } from "@/lib/api/errors";
import { isLoggingOut } from "@/lib/auth";

/** Session over (the one refresh attempt failed): go to /login and come back afterwards. */
function onError(error: unknown) {
  if (!isApiError(error, 401) || isLoggingOut() || window.location.pathname === "/login") return;
  const next = encodeURIComponent(window.location.pathname);
  // A full page load (not router.push) drops every cached response, patient data included.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`/login?next=${next}`);
}

export function makeQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // 4xx answers will not change on retry; network and 5xx errors get two more tries.
        retry: (count, error) => !(isApiError(error) && error.status < 500) && count < 2,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={300}>
        {children}
        <Toaster position="top-right" />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
