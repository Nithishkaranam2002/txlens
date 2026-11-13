"use client";

import { useQuery } from "@tanstack/react-query";
import { Compass } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, unwrap } from "@/lib/api/client";
import { formatDate } from "@/lib/format";

/** The public demo's guide under the sign-in form: which guest account shows what, until
 * when, and what to try first. Shown only when the API says this is the public demo; it
 * never contains a password. */
export function DemoGuide() {
  const demo = useQuery({
    queryKey: ["demo"],
    queryFn: async () => unwrap(await api.GET("/demo")),
    staleTime: Infinity,
    retry: false,
  });
  if (!demo.data?.guide) return null;
  const until = demo.data.guest_until;
  return (
    <Card className="w-full max-w-sm" aria-labelledby="demo-guide-title">
      <CardHeader>
        <CardTitle>
          <h2 id="demo-guide-title" className="flex items-center gap-2 text-base">
            <Compass aria-hidden className="size-4" /> Demo guide
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ul className="space-y-1">
          <li><span className="font-mono font-medium">guest.clinician</span>: worklist, patient pages, review queue</li>
          <li><span className="font-mono font-medium">guest.researcher</span>: cohorts, survival analysis, copilot</li>
        </ul>
        <p className="text-muted-foreground">
          Password: shared by the author on request.
          {until && <> Guest access is open until <time dateTime={until}>{formatDate(until)}</time>.</>}
        </p>
        <div>
          <p className="font-medium">Three things to try</p>
          <ol className="mt-1 list-decimal space-y-1 pl-5">
            <li>Open the top patient on the worklist and read why they are flagged.</li>
            <li>In the review queue, open a biopsy report, check the evidence boxes, approve it.</li>
            <li>As the researcher, run a survival analysis or ask a suggested copilot question.</li>
          </ol>
        </div>
      </CardContent>
    </Card>
  );
}
