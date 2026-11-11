import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { meKey } from "@/lib/auth";
import type { Me, Role } from "@/lib/roles";

export function testMe(role: Role): Me {
  return {
    user_id: "u1",
    username: `${role}.center_a`,
    display_name: "Test User",
    role,
    tenant_id: "center_a",
    tenant_name: "Test Center A",
  };
}

/** Render with a fresh query client; `role` pre-fills /me so role-aware components render. */
export function renderWithQuery(ui: ReactElement, role?: Role) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  if (role) client.setQueryData(meKey, testMe(role));
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

/** A JSON Response like FastAPI's, with a request id. */
export function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-123", ...headers },
  });
}
