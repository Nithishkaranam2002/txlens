"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  ACTION_LABEL,
  changedFields,
  decisionProblem,
  REASON_LABEL,
  useDecide,
  type DecisionAction,
  type ReasonCode,
  type ReviewDetail,
} from "@/lib/review";
import { SHORTCUTS, useReviewShortcuts } from "@/lib/use-review-shortcuts";

const SELECT = "h-9 w-full rounded-md border bg-card px-2 text-sm";

/**
 * The reviewer's form: the model's values (editable), an action, and a reason for anything
 * but approve. `onDone` runs after a decision is saved (the page moves to the next item).
 */
export function DecisionForm({
  detail,
  onDone,
  onNext,
  onPrevious,
  onFocusField,
}: {
  detail: ReviewDetail;
  onDone: () => void;
  onNext: () => void;
  onPrevious: () => void;
  onFocusField: (field: string | null) => void;
}) {
  const { item, fields, actions } = detail;
  const initial = Object.fromEntries(fields.map((f) => [f.name, f.value ?? ""]));
  const [values, setValues] = useState<Record<string, string>>(initial);
  const [action, setAction] = useState<DecisionAction>("approve");
  const [reason, setReason] = useState<ReasonCode | null>(null);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const firstField = useRef<HTMLInputElement | HTMLSelectElement | null>(null);
  const reasonField = useRef<HTMLSelectElement>(null);
  const decide = useDecide();
  // Curated rows stay open after a review: a clinician may correct or revert them later.
  const canDecide = actions.length > 0 && (item.status === "pending" || item.item_type === "curated_mapping");
  const readOnly = !canDecide;

  const submit = (chosen: DecisionAction = action) => {
    const changes = changedFields(fields, values);
    const why = decisionProblem(chosen, reason, note) ?? (chosen === "edit" && Object.keys(changes).length === 0 ? "Change at least one field, or approve." : null);
    setProblem(why);
    if (why) return;
    decide.mutate(
      { type: item.item_type, key: item.item_key, action: chosen, reason, note, changes },
      { onSuccess: onDone },
    );
  };

  useReviewShortcuts(
    {
      approve: () => canDecide && actions.includes("approve") && submit("approve"),
      edit: () => {
        if (!canDecide || !actions.includes("edit")) return;
        setAction("edit");
        firstField.current?.focus();
      },
      reject: () => {
        const choice = actions.includes("reject") ? "reject" : actions.includes("revert") ? "revert" : null;
        if (!canDecide || !choice) return;
        setAction(choice);
        setTimeout(() => reasonField.current?.focus());
      },
      next: onNext,
      previous: onPrevious,
      help: () => setHelp((shown) => !shown),
    },
    true,
  );

  const edit = (name: string, value: string) => {
    setValues((current) => ({ ...current, [name]: value }));
    if (actions.includes("edit")) setAction("edit");
  };

  return (
    <form
      aria-label="Review decision"
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <fieldset disabled={readOnly || decide.isPending} className="space-y-3">
        <legend className="text-sm font-medium">Values</legend>
        {fields.map((f, i) => {
          const id = `field-${f.name}`;
          const changed = values[f.name] !== (f.value ?? "");
          const ref = (el: HTMLInputElement | HTMLSelectElement | null) => {
            if (i === 0) firstField.current = el;
          };
          return (
            <div key={f.name} className="grid grid-cols-[10rem_1fr] items-center gap-2">
              <Label htmlFor={id} className="capitalize">
                {f.label}
              </Label>
              <div onFocus={() => onFocusField(f.name)} onMouseEnter={() => onFocusField(f.name)}>
                {f.choices ? (
                  <select id={id} ref={ref} className={SELECT} value={values[f.name]} onChange={(e) => edit(f.name, e.target.value)}>
                    {f.choices.map((choice) => (
                      <option key={choice} value={choice}>
                        {choice}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    id={id}
                    ref={ref}
                    value={values[f.name]}
                    inputMode={f.concept ? "numeric" : undefined}
                    onChange={(e) => edit(f.name, e.target.value)}
                    aria-describedby={`${id}-model`}
                  />
                )}
                <p id={`${id}-model`} className="mt-0.5 text-xs text-muted-foreground">
                  Model: {f.model_value ?? "not reported"}
                  {f.confidence != null && ` · confidence ${f.confidence.toFixed(2)}`}
                  {changed && <span className="ml-1 font-medium text-amber-700">edited</span>}
                </p>
              </div>
            </div>
          );
        })}
      </fieldset>

      {readOnly ? (
        <p className="rounded-md bg-muted p-3 text-sm" role="note">
          {actions.length === 0
            ? "Only clinicians review AI-curated mappings; you can see them but not change them."
            : "This item has been decided."}
        </p>
      ) : (
        <fieldset disabled={decide.isPending} className="space-y-3">
          <legend className="text-sm font-medium">Decision</legend>
          <div role="radiogroup" aria-label="Action" className="flex flex-wrap gap-2">
            {actions.map((choice) => (
              <label key={choice} className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm has-checked:border-primary has-checked:bg-accent">
                <input type="radio" name="action" value={choice} checked={action === choice} onChange={() => setAction(choice)} />
                {choice === "edit" ? "Edit" : ACTION_LABEL[choice]}
              </label>
            ))}
          </div>
          {action !== "approve" && (
            <div className="space-y-1.5">
              <Label htmlFor="reason">Reason (required)</Label>
              <select
                id="reason"
                ref={reasonField}
                className={SELECT}
                value={reason ?? ""}
                onChange={(e) => setReason((e.target.value || null) as ReasonCode | null)}
              >
                <option value="">Choose a reason…</option>
                {detail.reason_codes.map((code) => (
                  <option key={code} value={code}>
                    {REASON_LABEL[code]}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="note">Note{reason === "other" ? " (required)" : " (optional)"}</Label>
            <Textarea id="note" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
          </div>
          {problem && (
            <p role="alert" className="text-sm text-destructive">
              {problem}
            </p>
          )}
          <Button type="submit">{ACTION_LABEL[action]}</Button>
        </fieldset>
      )}

      <div className="text-xs text-muted-foreground">
        <button type="button" className="underline underline-offset-4" onClick={() => setHelp((shown) => !shown)} aria-expanded={help}>
          Keyboard shortcuts
        </button>
        {help && (
          <ul aria-label="Keyboard shortcuts" className="mt-2 grid grid-cols-[2rem_1fr] gap-1">
            {SHORTCUTS.map((s) => (
              <li key={s.key} className="contents">
                <kbd className="rounded border bg-muted px-1 text-center font-mono">{s.key}</kbd>
                <span>{s.does}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </form>
  );
}
