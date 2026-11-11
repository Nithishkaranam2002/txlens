import type { Metadata } from "next";

import { RoleGate } from "@/components/role-gate";

import { CohortBuilder } from "./cohort-builder";

export const metadata: Metadata = { title: "Cohorts" };

export default function CohortsPage() {
  return (
    <RoleGate area="cohorts">
      <CohortBuilder />
    </RoleGate>
  );
}
