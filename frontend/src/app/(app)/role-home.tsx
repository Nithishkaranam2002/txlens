"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { PageSkeleton } from "@/components/states";
import { useRole } from "@/lib/auth";
import { homeFor } from "@/lib/roles";

/** "/" sends each role to its first page. */
export function RoleHome() {
  const role = useRole();
  const router = useRouter();
  useEffect(() => {
    if (role) router.replace(homeFor(role));
  }, [role, router]);
  return <PageSkeleton />;
}
