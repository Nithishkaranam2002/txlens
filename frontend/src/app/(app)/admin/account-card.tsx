"use client";

import { ShieldCheck } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useMe } from "@/lib/auth";
import { asRole, ROLE_LABEL } from "@/lib/roles";

/** Admins manage the platform, not patients: this page shows who they are and nothing else. */
export function AccountCard() {
  const { data: me } = useMe();
  if (!me) return null;
  const role = asRole(me.role);
  const rows: [string, string][] = [
    ["Name", me.display_name],
    ["User name", me.username],
    ["Role", role ? ROLE_LABEL[role] : me.role],
    ["Center", me.tenant_name],
    ["Center id", me.tenant_id],
  ];
  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Admin</h1>
      <Alert>
        <ShieldCheck aria-hidden />
        <AlertTitle>You have no access to patient data</AlertTitle>
        <AlertDescription>
          The administrator role cannot open the worklist, patients or cohorts. User management
          is not part of this version of the API.
        </AlertDescription>
      </Alert>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Your account</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
            {rows.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="font-medium">{value}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
