"use client";

import type { Route } from "next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { asRole, type Me, type Role } from "@/lib/roles";

export const meKey = ["me"] as const;

let loggingOut = false;

/** True once logout has started. Until the page reloads, a 401 means "logged out", not "expired". */
export function isLoggingOut(): boolean {
  return loggingOut;
}

/** The logged-in user. A 401 (after one refresh attempt) sends the browser to /login. */
export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async () => unwrap(await api.GET("/me")),
    staleTime: 5 * 60_000,
  });
}

/** The current role, or null while /me is loading. */
export function useRole(): Role | null {
  const { data } = useMe();
  return data ? asRole(data.role) : null;
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: { username: string; password: string }) => {
      unwrap(await api.POST("/auth/login", { body }));
      return unwrap(await api.GET("/me")) as Me;
    },
    onSuccess: (me) => {
      client.clear();
      client.setQueryData(meKey, me);
    },
  });
}

/** Revoke the session, drop every cached response (patient data included) and go to /login. */
export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      // Queries refetched after the cookies are gone get 401s; they must not send the next
      // user to this user's page (/login?next=...).
      loggingOut = true;
      await api.POST("/auth/logout");
    },
    onSettled: () => {
      client.clear();
      // A full page load, so no patient data survives in memory after logout.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/login");
    },
  });
}

/** A same-site path to return to after login; anything else falls back to the role's home. */
export function safeNext(next: string | null): Route | null {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login")
    ? (next as Route)
    : null;
}
