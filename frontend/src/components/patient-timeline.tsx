import { Activity, HeartCrack, Microscope, Pill, Skull, Stethoscope } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDate } from "@/lib/format";
import type { BiopsyResult, NoteEvidence, Timeline } from "@/lib/types";

/** Diagnoses that mean the graft failed (named by the vocabulary, e.g. "Failed renal transplant"). */
const GRAFT_EVENT = /transplant failure|failed .*transplant|graft (failure|loss)|rejection/i;

const DIAGNOSES: Record<string, string> = {
  no_rejection: "No rejection", borderline: "Borderline", TCMR: "TCMR", ABMR: "ABMR",
  mixed: "Mixed (TCMR + ABMR)",
};
const STATUSES: Record<BiopsyResult["status"], string> = {
  extracted: "extracted, not reviewed", in_review: "in review", approved: "approved",
};

/** One line for a biopsy result: diagnosis and grade, key scores, and how far it was checked. */
export function biopsyResultText(result: BiopsyResult | null | undefined): string {
  if (!result) return "Result: awaiting document extraction";
  const diagnosis = result.diagnosis ? (DIAGNOSES[result.diagnosis] ?? result.diagnosis) : "unclear";
  const graded = result.grade && result.diagnosis !== "no_rejection" ? `${diagnosis} ${result.grade}` : diagnosis;
  const scores = Object.entries(result.scores).map(([name, value]) =>
    /^\d+$/.test(value) ? `${name}${value}` : `${name} ${value}`);
  return ["Result: " + graded, scores.join(" "), STATUSES[result.status]].filter(Boolean).join(" · ");
}

/** A condition read from clinic notes: how many notes, and the first one's evidence. */
export type FromNote = { count: number; evidence: NoteEvidence | null };
export type TimelineEvent = {
  date: string; kind: string; title: string; detail?: string; icon: ReactNode; fromNote?: FromNote;
};

/** Procedures, diagnoses, graft events and death as one dated list, newest first. */
export function timelineEvents(timeline: Timeline): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  for (const p of timeline.procedures) {
    if (p.kind === "transplant") {
      events.push({ date: p.date, kind: "Transplant", title: p.procedure ?? "Transplant", icon: <Activity className="size-4" /> });
    } else if (p.kind === "biopsy") {
      events.push({
        date: p.date, kind: "Biopsy", title: p.procedure ?? "Graft biopsy",
        detail: biopsyResultText(p.result), icon: <Microscope className="size-4" />,
      });
    }
  }
  for (const c of timeline.conditions) {
    const graft = GRAFT_EVENT.test(c.condition ?? "");
    events.push({
      date: c.date, kind: graft ? "Graft event" : "Diagnosis", title: c.condition ?? `Concept ${c.concept_id}`,
      icon: graft ? <HeartCrack className="size-4" /> : <Stethoscope className="size-4" />,
      fromNote: c.source === "note" ? { count: c.note_count ?? 1, evidence: c.evidence ?? null } : undefined,
    });
  }
  if (timeline.patient.death_date) {
    events.push({ date: timeline.patient.death_date, kind: "Death", title: "Death recorded", icon: <Skull className="size-4" /> });
  }
  return events.sort((a, b) => b.date.localeCompare(a.date));
}

function notesLabel(count: number): string {
  return `mentioned in ${count} ${count === 1 ? "note" : "notes"}`;
}

/** "from note" badge; with the evidence sentence in a popover when the role may see it. */
export function FromNoteBadge({ fromNote }: { fromNote: FromNote }) {
  const sentence = fromNote.evidence?.sentence;
  if (!sentence) {
    return (
      <span className="ml-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <Badge variant="outline">from note</Badge>
        {notesLabel(fromNote.count)}
      </span>
    );
  }
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="ml-2 align-middle" aria-label={`From note: show the sentence (${notesLabel(fromNote.count)})`}>
          <Badge variant="outline">from note</Badge>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start">
        <p className="text-xs text-muted-foreground">
          First note {formatDate(fromNote.evidence?.date ?? "")} · {notesLabel(fromNote.count)}
        </p>
        <blockquote className="border-l-2 pl-2 text-sm">{sentence}</blockquote>
        <p className="text-xs text-muted-foreground">Read by NLP from the note; check the note before acting on it.</p>
      </PopoverContent>
    </Popover>
  );
}

export function PatientTimeline({ timeline }: { timeline: Timeline }) {
  const events = timelineEvents(timeline);
  const drugs = [...timeline.drugs].sort((a, b) => b.start_date.localeCompare(a.start_date));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Clinical timeline</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground">No events recorded.</p>
          ) : (
            <ol className="relative space-y-4 border-l pl-6">
              {events.map((event, i) => (
                <li key={`${event.date}-${i}`} className="relative">
                  <span aria-hidden className="absolute top-0.5 -left-[2.1rem] flex size-6 items-center justify-center rounded-full border bg-card text-primary">
                    {event.icon}
                  </span>
                  <p className="text-xs text-muted-foreground">
                    <time dateTime={event.date}>{formatDate(event.date)}</time> · {event.kind}
                  </p>
                  <p className="text-sm font-medium">
                    {event.title}
                    {event.fromNote && <FromNoteBadge fromNote={event.fromNote} />}
                  </p>
                  {event.detail && <p className="text-sm text-muted-foreground">{event.detail}</p>}
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Medication history</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {drugs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No medications recorded.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Medications, most recent first</caption>
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th scope="col" className="pb-2"><Pill aria-hidden className="mr-1 inline size-3.5" />Drug</th>
                  <th scope="col" className="pb-2">Start</th>
                  <th scope="col" className="pb-2">End</th>
                </tr>
              </thead>
              <tbody>
                {drugs.map((drug, i) => (
                  <tr key={`${drug.concept_id}-${drug.start_date}-${i}`} className="border-t">
                    <td className="py-1.5 pr-3">{drug.drug ?? `Concept ${drug.concept_id}`}</td>
                    <td className="py-1.5 pr-3 whitespace-nowrap">{formatDate(drug.start_date)}</td>
                    <td className="py-1.5 whitespace-nowrap">{drug.end_date ? formatDate(drug.end_date) : "ongoing"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
