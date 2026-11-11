"use client";

import { Activity, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { SafetyBanner } from "@/components/banner";
import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLogout, useMe } from "@/lib/auth";
import { asRole, navFor, ROLE_LABEL } from "@/lib/roles";
import { cn } from "@/lib/utils";

/** Header with role-aware navigation, the safety banner, and the page in a <main> landmark. */
export function AppShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const logout = useLogout();
  const pathname = usePathname();
  const role = me.data ? asRole(me.data.role) : null;

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <SafetyBanner />
      <header className="border-b bg-card">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
          <Link href="/" className="flex items-center gap-2 font-semibold text-primary">
            <Activity aria-hidden className="size-5" />
            TxLens
          </Link>
          <nav aria-label="Main" className="flex items-center gap-1">
            {navFor(role).map((item) => {
              const current = pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={current ? "page" : undefined}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                    current && "bg-accent text-accent-foreground",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            {me.data ? (
              <span className="hidden text-right leading-tight sm:block">
                <span className="block font-medium">{me.data.display_name}</span>
                <span className="block text-xs text-muted-foreground">
                  {role ? ROLE_LABEL[role] : me.data.role} · {me.data.tenant_name}
                </span>
              </span>
            ) : (
              <Skeleton className="h-8 w-40" />
            )}
            <Button variant="outline" size="sm" onClick={() => logout.mutate()} disabled={logout.isPending}>
              <LogOut aria-hidden />
              Log out
            </Button>
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 outline-none">
        {me.isError ? <ErrorState error={me.error} onRetry={() => me.refetch()} /> : children}
      </main>
    </>
  );
}
