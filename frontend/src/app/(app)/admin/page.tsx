import type { Metadata } from "next";

import { RoleGate } from "@/components/role-gate";

import { AccountCard } from "./account-card";

export const metadata: Metadata = { title: "Admin" };

export default function AdminPage() {
  return (
    <RoleGate area="admin">
      <AccountCard />
    </RoleGate>
  );
}
