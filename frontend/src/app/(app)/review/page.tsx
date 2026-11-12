import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { QueueView } from "./queue-view";

export const metadata: Metadata = { title: "Review queue" };

export default function ReviewQueuePage() {
  return (
    <RoleGate area="queue">
      <Suspense fallback={<PageSkeleton />}>
        <QueueView />
      </Suspense>
    </RoleGate>
  );
}
