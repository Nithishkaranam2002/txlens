import type { Flag, Lab, SurveillanceConfig } from "@/lib/types";

/** Same month length the surveillance engine uses for "month N after transplant". */
export const DAYS_PER_MONTH = 30.4375;
/** Evidence roles that point at the result that triggered a flag. */
const TRIGGER_ROLES = new Set(["latest", "trend", "first_positive"]);

export type Series = { dates: string[]; values: number[] };
export type Level = { value: number; label: string; severity: string };
export type Band = { start: string; end: string; low: number; high: number; label: string };

/** Lab results split by marker (oldest first), skipping results with no value. */
export function seriesByLab(labs: Lab[]): Record<string, Series> {
  const out: Record<string, Series> = {};
  for (const lab of [...labs].sort((a, b) => a.date.localeCompare(b.date))) {
    if (lab.value === null) continue;
    out[lab.lab] ??= { dates: [], values: [] };
    out[lab.lab].dates.push(lab.date);
    out[lab.lab].values.push(lab.value);
  }
  return out;
}

type Rule = SurveillanceConfig["rules"][number];

function rulesFor<K extends Rule["kind"]>(config: SurveillanceConfig, kind: K, marker: string) {
  return config.rules.filter(
    (rule): rule is Extract<Rule, { kind: K }> => rule.kind === kind && "marker" in rule && rule.marker === marker,
  );
}

/** Threshold lines from the config, e.g. dd-cfDNA >= 0.5% and >= 1.0% for kidney. */
export function thresholdLevels(config: SurveillanceConfig, marker: string): Level[] {
  return rulesFor(config, "threshold", marker).flatMap((rule) =>
    rule.levels.map((level) => ({
      value: level.at_or_above,
      label: `≥ ${level.at_or_above} (${level.meaning})`,
      severity: level.severity,
    })),
  );
}

/** The value from which a result counts as positive (e.g. DSA MFI >= 1000). */
export function positiveCutoff(config: SurveillanceConfig, marker: string): number | null {
  return rulesFor(config, "new_positive", marker)[0]?.positive_at_or_above ?? null;
}

function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate.slice(0, 10)}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + Math.round(days));
  return date.toISOString().slice(0, 10);
}

/**
 * Target ranges by phase as date spans from the transplant date. The last phase is open-ended,
 * so it runs to `until` (the last result shown).
 */
export function targetBands(config: SurveillanceConfig, marker: string, transplantDate: string, until: string): Band[] {
  const rule = rulesFor(config, "range_by_phase", marker)[0];
  if (!rule) return [];
  return rule.phases
    .map((phase) => {
      const start = addDays(transplantDate, phase.from_month * DAYS_PER_MONTH);
      const end = phase.to_month == null ? until : addDays(transplantDate, phase.to_month * DAYS_PER_MONTH);
      const months = phase.to_month == null ? `month ${phase.from_month}+` : `months ${phase.from_month}–${phase.to_month}`;
      return { start, end, low: phase.low, high: phase.high, label: `Target ${phase.low}–${phase.high} (${months})` };
    })
    .filter((band) => band.start < band.end);
}

/** Dates of results that triggered a flag for this marker (to mark them on the chart). */
export function flaggedDates(flags: Flag[], marker: string): Set<string> {
  return new Set(
    flags.flatMap((flag) =>
      flag.evidence.filter((e) => e.marker === marker && TRIGGER_ROLES.has(e.role)).map((e) => e.date),
    ),
  );
}

/** The points of a series whose dates were flagged. */
export function flaggedPoints(series: Series, dates: Set<string>): Series {
  const out: Series = { dates: [], values: [] };
  series.dates.forEach((date, i) => {
    if (dates.has(date)) {
      out.dates.push(date);
      out.values.push(series.values[i]);
    }
  });
  return out;
}
