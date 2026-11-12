import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { CopilotView } from "./copilot-view";

export const metadata: Metadata = { title: "Research copilot" };

export default function CopilotPage() {
  return (
    <RoleGate area="copilot">
      <Suspense fallback={<PageSkeleton />}>
        <CopilotView />
      </Suspense>
    </RoleGate>
  );
}
