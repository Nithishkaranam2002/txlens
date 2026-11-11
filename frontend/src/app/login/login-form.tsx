"use client";

import { Activity, AlertCircle, Loader2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isApiError } from "@/lib/api/errors";
import { safeNext, useLogin } from "@/lib/auth";
import { asRole, landingFor } from "@/lib/roles";

/** A login failure in words a user can act on. */
export function loginErrorMessage(error: unknown): string {
  if (isApiError(error, 429)) {
    const minutes = Math.ceil((error.retryAfterSeconds ?? 900) / 60);
    return `Too many failed sign-in attempts. Try again in ${minutes} minutes.`;
  }
  if (isApiError(error, 401)) return "Invalid user name or password.";
  if (isApiError(error)) return `Sign-in failed: ${error.message}`;
  return "Cannot reach the server. Check your connection and try again.";
}

export function LoginForm() {
  const login = useLogin();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(
      { username, password },
      { onSuccess: (me) => router.replace(landingFor(asRole(me.role), next)) },
    );
  };

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <div className="mb-2 flex items-center gap-2 text-primary">
          <Activity aria-hidden className="size-6" />
          <span className="text-lg font-semibold">TxLens</span>
        </div>
        <CardTitle>
          <h1>Sign in</h1>
        </CardTitle>
        <CardDescription>Transplant surveillance and cohort workbench.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4" noValidate>
          {login.isError && (
            <Alert variant="destructive" role="alert">
              <AlertCircle aria-hidden />
              <AlertDescription>{loginErrorMessage(login.error)}</AlertDescription>
            </Alert>
          )}
          <div className="space-y-2">
            <Label htmlFor="username">User name</Label>
            <Input
              id="username"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              aria-invalid={login.isError || undefined}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={login.isError || undefined}
            />
          </div>
          <Button type="submit" className="w-full" disabled={login.isPending || !username || !password}>
            {login.isPending && <Loader2 aria-hidden className="animate-spin" />}
            Sign in
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
