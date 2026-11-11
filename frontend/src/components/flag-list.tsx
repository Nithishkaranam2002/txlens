import { ReviewDialog } from "@/components/review-dialog";
import { SeverityBadge, StatusBadge } from "@/components/severity-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/format";
import type { FlagEvent, PatientFlag } from "@/lib/types";

const EVENT_TEXT: Record<FlagEvent["event"], string> = {
  raised: "Raised by the surveillance run",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  cleared: "Cleared (the rule no longer fires)",
};

/** Every flag of the patient, newest first, with its status history. */
export function FlagList({ flags, canReview, subject }: { flags: PatientFlag[]; canReview: boolean; subject: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Surveillance flags</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {flags.length === 0 ? (
          <p className="text-sm text-muted-foreground">This patient has no surveillance flags.</p>
        ) : (
          <ul className="divide-y">
            {flags.map((flag) => {
              const open = flag.status !== "resolved" && !flag.cleared;
              return (
                <li key={flag.flag_id} className="space-y-2 py-3 first:pt-0">
                  <div className="flex flex-wrap items-start gap-2">
                    <SeverityBadge severity={flag.severity} />
                    <StatusBadge status={flag.status} cleared={flag.cleared} />
                    <p className="min-w-60 flex-1 text-sm">{flag.reason}</p>
                    {canReview && open && (
                      <span className="flex gap-1.5">
                        {flag.status === "new" && <ReviewDialog flag={flag} action="acknowledge" subject={subject} />}
                        <ReviewDialog flag={flag} action="resolve" subject={subject} />
                      </span>
                    )}
                  </div>
                  <ol className="ml-1 space-y-0.5 border-l pl-3 text-xs text-muted-foreground" aria-label="Status history">
                    {flag.history.map((event, i) => (
                      <li key={i}>
                        <time dateTime={event.at}>{formatDate(event.at)}</time> — {EVENT_TEXT[event.event]}
                        {event.by && ` by ${event.by}`}
                        {event.note && <span className="text-foreground">: “{event.note}”</span>}
                      </li>
                    ))}
                  </ol>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
