import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { PatientView } from "./patient-view";

// The title never names the patient: browser history and tabs must not hold identifiers.
export const metadata: Metadata = { title: "Patient" };

export default function PatientPage({ params }: PageProps<"/patients/[id]">) {
  return (
    <RoleGate area="patients">
      <Suspense fallback={<PageSkeleton />}>
        <PatientView params={params} />
      </Suspense>
    </RoleGate>
  );
}
