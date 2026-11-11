import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { WorklistView } from "./worklist-view";

export const metadata: Metadata = { title: "Worklist" };

export default function WorklistPage() {
  return (
    <RoleGate area="worklist">
      <Suspense fallback={<PageSkeleton />}>
        <WorklistView />
      </Suspense>
    </RoleGate>
  );
}
