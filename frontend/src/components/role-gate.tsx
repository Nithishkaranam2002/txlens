"use client";

import type { ReactNode } from "react";

import { NotAvailable, PageSkeleton } from "@/components/states";
import { useRole } from "@/lib/auth";
import { can, type Area } from "@/lib/roles";

/** Renders children only for roles that may use this area. The API is still the real guard. */
export function RoleGate({ area, children }: { area: Area; children: ReactNode }) {
  const role = useRole();
  if (role === null) return <PageSkeleton />; // until /me arrives
  return can(role, area) ? children : <NotAvailable />;
}
