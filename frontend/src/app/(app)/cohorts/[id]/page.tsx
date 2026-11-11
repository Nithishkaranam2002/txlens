import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { CohortView } from "./cohort-view";

export const metadata: Metadata = { title: "Cohort" };

export default function CohortPage({ params }: PageProps<"/cohorts/[id]">) {
  return (
    <RoleGate area="cohorts">
      <Suspense fallback={<PageSkeleton />}>
        <CohortView params={params} />
      </Suspense>
    </RoleGate>
  );
}
