"use client";

import { Search } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from "react";

import { isApiError } from "@/lib/api/errors";
import { ORGAN_LABEL, patientName, patientDetail } from "@/lib/format";
import { SEARCH_MIN, usePatientSearch } from "@/lib/queries";
import { seesIdentifiers, type Role } from "@/lib/roles";
import type { Patient } from "@/lib/types";
import { useDebounced } from "@/lib/use-debounced";
import { typing } from "@/lib/use-review-shortcuts";
import { cn } from "@/lib/utils";

/** "/" anywhere outside a field focuses the search, as on many sites. */
function useSlashToFocus(input: RefObject<HTMLInputElement | null>) {
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "/" || event.altKey || event.ctrlKey || event.metaKey || typing(event.target)) return;
      event.preventDefault();
      input.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [input]);
}

/**
 * Top-bar patient search (a combobox): up to ten patients of the user's center. Clinicians
 * search names and MRNs; researchers and data stewards search pseudonyms only (the API
 * enforces this). Arrow keys move, Enter opens, Esc closes; "/" focuses it.
 */
export function PatientSearch({ role }: { role: Role }) {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const term = useDebounced(query.trim(), 300);
  const search = usePatientSearch(term);
  useSlashToFocus(input);

  const ready = query.trim().length >= SEARCH_MIN;
  const results: Patient[] = ready && term.length >= SEARCH_MIN ? (search.data ?? []) : [];
  const current = Math.min(active, results.length - 1);
  const expanded = open && ready;
  const optionId = (index: number) => `${listId}-option-${index}`;

  const choose = (patient: Patient) => {
    setQuery("");
    setOpen(false);
    input.current?.blur();
    router.push(`/patients/${patient.patient_id}` as Route);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      if (results.length) setActive((Math.max(current, 0) + step + results.length) % results.length);
    } else if (event.key === "Enter" && expanded && results[current]) {
      event.preventDefault();
      choose(results[current]);
    } else if (event.key === "Escape") {
      if (expanded) setOpen(false);
      else {
        setQuery("");
        input.current?.blur();
      }
    }
  };

  const status = search.isError
    ? isApiError(search.error, 429)
      ? "Too many searches. Wait a moment and try again."
      : "Search failed. Try again."
    : term !== query.trim() || search.isFetching
      ? "Searching…"
      : results.length === 0
        ? "No patients match"
        : null;

  return (
    <div className="relative w-full max-w-xs">
      <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <input
        ref={input}
        type="search"
        role="combobox"
        aria-label="Search patients"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={expanded && current >= 0 ? optionId(current) : undefined}
        aria-keyshortcuts="/"
        autoComplete="off"
        spellCheck={false}
        placeholder={seesIdentifiers(role) ? "Search patients (name or MRN)  /" : "Search pseudonym (R-…)  /"}
        className="h-8 w-full rounded-md border bg-background pr-2 pl-8 text-sm placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      <ul
        id={listId}
        role="listbox"
        aria-label="Matching patients"
        hidden={!expanded}
        className="absolute top-full right-0 left-0 z-50 mt-1 max-h-96 overflow-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
      >
        {results.map((patient, index) => (
          <li
            key={patient.patient_id}
            id={optionId(index)}
            role="option"
            aria-selected={index === current}
            className={cn("cursor-pointer rounded-sm px-2 py-1.5 text-sm", index === current && "bg-accent text-accent-foreground")}
            // mousedown would blur the input (closing the list) before the click lands.
            onMouseDown={(event) => event.preventDefault()}
            onMouseEnter={() => setActive(index)}
            onClick={() => choose(patient)}
          >
            <span className="block font-medium">{patientName(patient)}</span>
            <span className="block text-xs text-muted-foreground">
              {[patientDetail(patient), patient.organ ? ORGAN_LABEL[patient.organ] : null].filter(Boolean).join(" · ")}
            </span>
          </li>
        ))}
        {status && (
          <li role="presentation" aria-live="polite" className="px-2 py-1.5 text-sm text-muted-foreground">
            {status}
          </li>
        )}
      </ul>
    </div>
  );
}
