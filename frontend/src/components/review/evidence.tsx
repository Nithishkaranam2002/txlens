"use client";

import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import { scaleBox, type DocumentPane, type MappingPane, type SentencePane } from "@/lib/review";

const API = "/api";

/** The page image with a box per extracted field; the active field's box stands out. */
function PageImage({
  url,
  page,
  size,
  boxes,
  active,
}: {
  url: string;
  page: number;
  size: number[];
  boxes: { field: string; bbox: number[] }[];
  active: string | null;
}) {
  const ref = useRef<HTMLImageElement>(null);
  const [shown, setShown] = useState<{ width: number; height: number } | null>(null);
  const measure = () => {
    const image = ref.current;
    if (image?.clientWidth) setShown({ width: image.clientWidth, height: image.clientHeight });
  };
  useEffect(() => {
    const image = ref.current;
    if (!image || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (image.clientWidth) setShown({ width: image.clientWidth, height: image.clientHeight });
    });
    observer.observe(image);
    return () => observer.disconnect();
  }, []);
  return (
    <figure className="relative w-full">
      {/* eslint-disable-next-line @next/next/no-img-element -- an authenticated API image, not a static asset */}
      <img ref={ref} src={`${API}${url}`} alt={`Page ${page} of the pathology report`} className="w-full rounded border" onLoad={measure} />
      {shown &&
        boxes.map(({ field, bbox }) => {
          const box = scaleBox(bbox, size, shown);
          return (
            <div
              key={field}
              data-testid={`box-${field}`}
              aria-hidden
              className={cn(
                "pointer-events-none absolute rounded-sm border-2",
                field === active ? "border-amber-500 bg-amber-300/30" : "border-primary/60",
              )}
              style={{ left: box.left - 2, top: box.top - 2, width: box.width + 4, height: box.height + 4 }}
            />
          );
        })}
      <figcaption className="sr-only">Highlighted boxes show where each field was read.</figcaption>
    </figure>
  );
}

/** Clinicians: the full page with boxes. Data stewards: redacted lines and cropped line images. */
export function DocumentEvidence({ pane, active }: { pane: DocumentPane; active: string | null }) {
  const evidence = pane.evidence;
  if (evidence.view === "full") {
    return (
      <div className="space-y-3">
        {evidence.page_urls.map((url, i) => (
          <PageImage
            key={url}
            url={url}
            page={i + 1}
            size={evidence.page_sizes[i]}
            active={active}
            boxes={evidence.fields
              .filter((f) => f.page === i + 1 && f.bbox)
              .map((f) => ({ field: f.field, bbox: f.bbox as number[] }))}
          />
        ))}
      </div>
    );
  }
  return (
    <div className="space-y-2" aria-label="Redacted evidence">
      <p className="text-sm text-muted-foreground">
        De-identified view: the lines each value was read from, identifiers masked. Lines in the report header are
        never shown as images.
      </p>
      <ul className="space-y-2">
        {evidence.fields.map((f) => (
          <li
            key={f.field}
            className={cn("rounded-md border p-2", f.field === active && "border-amber-500 bg-amber-50")}
          >
            <div className="text-xs font-medium text-muted-foreground">{f.field.replace(/_/g, " ")}</div>
            {pane.crop_urls[f.field] ? (
              // eslint-disable-next-line @next/next/no-img-element -- an authenticated API image
              <img src={`${API}${pane.crop_urls[f.field]}`} alt={`Redacted line for ${f.field.replace(/_/g, " ")}`} className="mt-1 max-w-full rounded border" />
            ) : null}
            <p className="mt-1 font-mono text-xs">{f.line_text ?? "—"}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The sentence with the mention marked. */
export function SentenceEvidence({ pane }: { pane: SentencePane }) {
  const { sentence, highlight_start: start, highlight_end: end } = pane;
  return (
    <figure className="space-y-2">
      <figcaption className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Badge variant="outline">{pane.view === "identified" ? "As written" : "De-identified"}</Badge>
        Note of {formatDate(pane.note_date)}
        {pane.section && <span>· {pane.section.replace(/_/g, " ")}</span>}
      </figcaption>
      <blockquote className="rounded-md border bg-card p-4 text-base leading-relaxed">
        {sentence.slice(0, start)}
        <mark className="rounded bg-amber-200 px-0.5">{sentence.slice(start, end)}</mark>
        {sentence.slice(end)}
      </blockquote>
    </figure>
  );
}

function Concept({ concept }: { concept: MappingPane["concept"] }) {
  return concept ? (
    <span>
      {concept.name ?? "Unknown concept"} <span className="text-muted-foreground">({concept.concept_id})</span>
    </span>
  ) : (
    <span className="text-muted-foreground">none</span>
  );
}

/** A source text and its concept; for AI-curated rows, every earlier review. */
export function MappingEvidence({ pane }: { pane: MappingPane }) {
  return (
    <div className="space-y-3 text-sm">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted-foreground">Source text</dt>
        <dd className="font-medium">“{pane.source_value}”</dd>
        {pane.source_vocabulary_id && (
          <>
            <dt className="text-muted-foreground">Vocabulary</dt>
            <dd>{pane.source_vocabulary_id}</dd>
          </>
        )}
        {pane.description && (
          <>
            <dt className="text-muted-foreground">Description</dt>
            <dd>{pane.description}</dd>
          </>
        )}
        <dt className="text-muted-foreground">{pane.label ? "Concept now" : "Suggested concept"}</dt>
        <dd>
          <Concept concept={pane.concept} />
          {pane.suggestion_score != null && (
            <span className="ml-2 text-muted-foreground">score {pane.suggestion_score.toFixed(2)}</span>
          )}
        </dd>
        {pane.label && (
          <>
            <dt className="text-muted-foreground">Label</dt>
            <dd>
              <Badge variant={pane.label === "clinician-reviewed" ? "default" : "secondary"}>{pane.label}</Badge>
            </dd>
          </>
        )}
        {pane.reason && (
          <>
            <dt className="text-muted-foreground">Why in review</dt>
            <dd>
              {pane.reason}
              {pane.rows != null && ` (${pane.rows} rows)`}
            </dd>
          </>
        )}
      </dl>
      {pane.history.length > 0 && (
        <table className="w-full text-left text-xs">
          <caption className="mb-1 text-left font-medium">Review history (shared by every center)</caption>
          <thead>
            <tr>
              <th scope="col" className="py-1 pr-3">When</th>
              <th scope="col" className="py-1 pr-3">Action</th>
              <th scope="col" className="py-1 pr-3">Concept</th>
              <th scope="col" className="py-1 pr-3">Center</th>
              <th scope="col" className="py-1">Reason</th>
            </tr>
          </thead>
          <tbody>
            {pane.history.map((h, i) => (
              <tr key={i} className="border-t">
                <td className="py-1 pr-3">{formatDate(h.decided_at)}</td>
                <td className="py-1 pr-3">{h.action}</td>
                <td className="py-1 pr-3">
                  {h.old_concept.concept_id} → {h.new_concept.concept_id}
                </td>
                <td className="py-1 pr-3">{h.reviewer_center}</td>
                <td className="py-1">{h.reason_code ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
