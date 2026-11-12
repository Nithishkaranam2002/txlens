import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { MetricsView } from "./metrics-view";

export const metadata: Metadata = { title: "Review metrics" };

export default function ReviewMetricsPage() {
  return (
    <RoleGate area="metrics">
      <Suspense fallback={<PageSkeleton />}>
        <MetricsView />
      </Suspense>
    </RoleGate>
  );
}
