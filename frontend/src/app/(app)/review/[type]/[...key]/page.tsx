import type { Metadata } from "next";
import { Suspense } from "react";

import { RoleGate } from "@/components/role-gate";
import { PageSkeleton } from "@/components/states";

import { ItemView } from "./item-view";

// The title never names the patient or the item: history and tabs must not hold identifiers.
export const metadata: Metadata = { title: "Review item" };

export default function ReviewItemPage({ params }: PageProps<"/review/[type]/[...key]">) {
  return (
    <RoleGate area="queue">
      <Suspense fallback={<PageSkeleton />}>
        <ItemView params={params} />
      </Suspense>
    </RoleGate>
  );
}
