import { AlertTriangle, ExternalLink, ShieldAlert } from "lucide-react";
import { Fragment } from "react";

import { AttritionChart, SuppressedCount } from "@/components/charts/attrition-chart";
import { Count, KmChart } from "@/components/charts/survival-charts";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { pieces, type Citation, type ToolDisplay, type ToolEvent, type Turn } from "@/lib/copilot";

/** "searched concepts → built cohort → ran survival": what the copilot did, in order. */
export function ToolTrace({ tools }: { tools: ToolEvent[] }) {
  if (tools.length === 0) return null;
  return (
    <ol aria-label="Steps the copilot took" className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
      {tools.map((tool, i) => (
        <li key={tool.step} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden>→</span>}
          <span className={`rounded-full border px-2 py-0.5 ${tool.ok ? "" : "border-destructive/50 text-destructive"}`}>
            {tool.label}
            {!tool.ok && <span className="sr-only"> (failed)</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Cell({ value }: { value: string | number | boolean | null }) {
  if (typeof value === "string" && (value.startsWith("<") || value === "withheld")) return <SuppressedCount value={value} />;
  if (typeof value === "number") return <>{Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(3)}</>;
  return <>{value === null ? "—" : String(value)}</>;
}

function Display({ display }: { display: ToolDisplay }) {
  switch (display.kind) {
    case "attrition":
      return <AttritionChart preview={display.preview} />;
    case "survival":
      return (
        <div className="space-y-2">
          <p className="text-sm">
            {display.report.population} · <Count value={display.report.n} /> patients, <Count value={display.report.events} /> graft losses ·
            follow-up from day {display.report.landmark_days}
          </p>
          <KmChart curves={display.report.km} />
        </div>
      );
    case "table":
      return (
        <div className="max-h-72 overflow-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>{display.columns.map((c) => <TableHead key={c}>{c}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {display.rows.map((row, i) => (
                <TableRow key={i}>{row.map((v, j) => <TableCell key={j}><Cell value={v} /></TableCell>)}</TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      );
  }
}

/** Each tool call: its error, its SQL ("Show SQL"), its chart or table, and its notices. */
export function ToolDetails({ tools }: { tools: ToolEvent[] }) {
  return (
    <div className="space-y-3">
      {tools.map((tool) => (
        <div key={tool.step} className="space-y-2">
          {tool.error && (
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" /> {tool.label}: {tool.error}
            </p>
          )}
          {tool.sql && (
            <details className="text-xs">
              <summary className="cursor-pointer font-medium text-primary">Show SQL</summary>
              <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 whitespace-pre-wrap">{tool.sql}</pre>
            </details>
          )}
          {tool.ok && tool.display && <Display display={tool.display} />}
          {tool.notices.map((notice) => <p key={notice} className="text-xs text-muted-foreground">{notice}</p>)}
        </div>
      ))}
    </div>
  );
}

const BULLET = /^\s*[-•*] /;

function PmidLink({ pmid, citations }: { pmid: number; citations: Citation[] }) {
  const index = citations.findIndex((c) => c.pmid === pmid);
  return (
    <a href={`#cite-${pmid}`} className="align-super text-xs font-medium text-primary hover:underline" aria-label={`Source PMID ${pmid}`}>
      [{index >= 0 ? index + 1 : pmid}]
    </a>
  );
}

/** The answer: paragraphs and bullets, bold, and citations as links to the sources below. */
export function AnswerText({ text, citations }: { text: string; citations: Citation[] }) {
  const lines = text.split("\n").filter((line) => line.trim());
  const render = (line: string) =>
    pieces(line).map((p, i) =>
      p.kind === "pmid" ? <PmidLink key={i} pmid={p.pmid} citations={citations} />
        : p.kind === "bold" ? <strong key={i}>{p.text}</strong>
          : <Fragment key={i}>{p.text}</Fragment>,
    );
  // Consecutive bullet lines form one list; other lines are paragraphs, in their order.
  const blocks: string[][] = [];
  for (const line of lines) {
    const bullet = BULLET.test(line);
    const last = blocks.at(-1);
    if (bullet && last && BULLET.test(last[0])) last.push(line);
    else blocks.push([line]);
  }
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {blocks.map((block, i) =>
        BULLET.test(block[0]) ? (
          <ul key={i} className="list-disc space-y-1 pl-5">{block.map((l, j) => <li key={j}>{render(l.replace(BULLET, ""))}</li>)}</ul>
        ) : (
          <p key={i}>{render(block[0])}</p>
        ),
      )}
    </div>
  );
}

/** Cited abstracts: title, journal, year, PubMed link, and the sentences used, verbatim. */
export function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <section aria-label="Sources" className="space-y-2 border-t pt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Sources</h3>
      <ol className="space-y-2 text-xs">
        {citations.map((c, i) => (
          <li key={c.pmid} id={`cite-${c.pmid}`} className="space-y-1">
            <p>
              <span className="font-medium">[{i + 1}]</span> {c.title} <span className="text-muted-foreground">{c.journal}{c.year ? `, ${c.year}` : ""}.</span>{" "}
              <a href={c.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline">
                PMID {c.pmid}<ExternalLink aria-hidden className="size-3" /><span className="sr-only"> (opens PubMed in a new tab)</span>
              </a>
            </p>
            <details>
              <summary className="cursor-pointer text-muted-foreground">Quoted from the abstract</summary>
              <blockquote className="mt-1 border-l-2 pl-2 text-muted-foreground">{c.snippet}</blockquote>
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** One answer as a whole: trace, tool details, the text (or refusal / error), sources. */
export function AnswerBody({ turn }: { turn: Turn }) {
  return (
    <div className="space-y-3">
      <ToolTrace tools={turn.tools} />
      <ToolDetails tools={turn.tools} />
      {turn.refusal ? (
        <p className="flex items-start gap-2 text-sm"><ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />{turn.refusal}</p>
      ) : (
        turn.text && <AnswerText text={turn.text} citations={turn.citations} />
      )}
      {turn.error && <p role="alert" className="text-sm text-destructive">{turn.error}</p>}
      <Citations citations={turn.citations} />
    </div>
  );
}
