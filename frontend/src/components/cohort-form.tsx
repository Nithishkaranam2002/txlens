"use client";

import { Plus, Trash2 } from "lucide-react";
import type { FormEvent } from "react";

import { ConceptSearch } from "@/components/concept-search";
import { FilterSelect } from "@/components/filter-select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LAB_OPTIONS, NEW_LAB_RULE, OP_OPTIONS, type CohortForm, type LabRuleDraft } from "@/lib/cohort-form";

const ORGANS = [{ value: "kidney", label: "Kidney" }, { value: "heart", label: "Heart" }] as const;
const DONORS = [{ value: "living", label: "Living" }, { value: "deceased", label: "Deceased" }] as const;

function NumberField({ id, label, value, onChange, min, max, className }: {
  id: string; label: string; value: string; onChange: (v: string) => void; min?: number; max?: number; className?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" inputMode="numeric" min={min} max={max} value={value}
        onChange={(e) => onChange(e.target.value)} className={className ?? "w-28 bg-card"} />
    </div>
  );
}

function LabRuleRow({ index, rule, onChange, onRemove }: {
  index: number; rule: LabRuleDraft; onChange: (r: LabRuleDraft) => void; onRemove: () => void;
}) {
  const id = `lab-${index}`;
  const unit = LAB_OPTIONS.find((o) => o.value === rule.lab)?.unit;
  return (
    <fieldset className="flex flex-wrap items-end gap-3 rounded-md border bg-card p-3">
      <legend className="px-1 text-xs text-muted-foreground">Lab rule {index + 1}</legend>
      <FilterSelectRequired id={`${id}-lab`} label="Lab" value={rule.lab} options={LAB_OPTIONS} onChange={(lab) => onChange({ ...rule, lab })} />
      <FilterSelectRequired id={`${id}-op`} label="Operator" value={rule.op} options={OP_OPTIONS} onChange={(op) => onChange({ ...rule, op })} width="w-20" />
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-value`}>Value{unit ? ` (${unit})` : ""}</Label>
        <Input id={`${id}-value`} type="number" step="any" value={rule.value} onChange={(e) => onChange({ ...rule, value: e.target.value })} className="w-28 bg-card" />
      </div>
      <NumberField id={`${id}-from`} label="From day" min={0} value={rule.dayFrom} onChange={(dayFrom) => onChange({ ...rule, dayFrom })} className="w-24 bg-card" />
      <NumberField id={`${id}-to`} label="To day" min={0} value={rule.dayTo} onChange={(dayTo) => onChange({ ...rule, dayTo })} className="w-24 bg-card" />
      <Button type="button" variant="ghost" size="icon" onClick={onRemove} aria-label={`Remove lab rule ${index + 1}`}>
        <Trash2 aria-hidden />
      </Button>
    </fieldset>
  );
}

/** A select without an "All" choice (lab rule fields always have a value). */
function FilterSelectRequired<T extends string>(props: {
  id: string; label: string; value: T; options: readonly { value: T; label: string }[]; onChange: (v: T) => void; width?: string;
}) {
  return <FilterSelect {...props} allowAll={false} onChange={(v) => v && props.onChange(v)} />;
}

/** The cohort rule form. Every rule narrows the cohort; the preview shows how many remain. */
export function CohortFormFields({ form, onChange, onSubmit, pending, wholeYearsOnly }: {
  form: CohortForm; onChange: (f: CohortForm) => void; onSubmit: () => void; pending: boolean; wholeYearsOnly: boolean;
}) {
  const set = <K extends keyof CohortForm>(key: K, value: CohortForm[K]) => onChange({ ...form, [key]: value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };
  return (
    <form onSubmit={submit} className="space-y-5" aria-label="Cohort rules">
      <div className="flex flex-wrap gap-4">
        <FilterSelect id="cohort-organ" label="Organ" value={form.organ} options={ORGANS} onChange={(v) => set("organ", v)} />
        <FilterSelect id="cohort-donor" label="Donor type" value={form.donorType} options={DONORS} onChange={(v) => set("donorType", v)} />
        <NumberField id="age-min" label="Age from" min={0} max={120} value={form.ageMin} onChange={(v) => set("ageMin", v)} />
        <NumberField id="age-max" label="Age to" min={0} max={120} value={form.ageMax} onChange={(v) => set("ageMax", v)} />
        <NumberField id="year-from" label="Transplant year from" min={1990} max={2100} value={form.yearFrom} onChange={(v) => set("yearFrom", v)} />
        <NumberField id="year-to" label="Transplant year to" min={1990} max={2100} value={form.yearTo} onChange={(v) => set("yearTo", v)} />
      </div>
      {wholeYearsOnly && <p className="text-xs text-muted-foreground">Researchers filter by whole transplant years only (privacy rule).</p>}

      <div className="space-y-1.5">
        <p className="text-sm font-medium" id="conditions-label">Conditions (any of; includes more specific concepts)</p>
        <ConceptSearch selected={form.conditions} onChange={(conditions) => set("conditions", conditions)} />
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">Lab rules (all must match)</p>
        {form.labs.map((rule, i) => (
          <LabRuleRow key={i} index={i} rule={rule}
            onChange={(next) => set("labs", form.labs.map((r, j) => (j === i ? next : r)))}
            onRemove={() => set("labs", form.labs.filter((_, j) => j !== i))} />
        ))}
        <Button type="button" variant="outline" size="sm" onClick={() => set("labs", [...form.labs, NEW_LAB_RULE])} disabled={form.labs.length >= 10}>
          <Plus aria-hidden /> Add lab rule
        </Button>
      </div>

      <Button type="submit" disabled={pending}>Preview cohort</Button>
    </form>
  );
}
