import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { SurvivalView } from "./survival-view";

export const metadata: Metadata = { title: "Survival analysis" };

export default function SurvivalPage() {
  return (
    <RoleGate area="survival">
      <Suspense fallback={<PageSkeleton />}>
        <SurvivalView />
      </Suspense>
    </RoleGate>
  );
}
