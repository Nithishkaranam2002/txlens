"use client";

import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useConceptSearch } from "@/lib/queries";
import type { ConceptHit } from "@/lib/types";

/** The value after the user stops typing for `ms`, so each keystroke does not hit the API. */
function useDebounced(value: string, ms: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/** Search SNOMED conditions and pick several; a pick also matches its more specific concepts. */
export function ConceptSearch({ selected, onChange }: { selected: ConceptHit[]; onChange: (next: ConceptHit[]) => void }) {
  const [query, setQuery] = useState("");
  const search = useConceptSearch(useDebounced(query, 300));
  const chosen = new Set(selected.map((c) => c.concept_id));
  const hits = (search.data ?? []).filter((hit) => !chosen.has(hit.concept_id));

  return (
    <div className="space-y-2">
      <Command shouldFilter={false} className="rounded-md border bg-card" label="Search conditions">
        <CommandInput
          value={query}
          onValueChange={setQuery}
          placeholder="Search by name or SNOMED code, e.g. diabetes"
          aria-label="Search conditions"
        />
        {query.trim().length >= 2 && (
          <CommandList>
            <CommandEmpty>{search.isFetching ? "Searching…" : "No matching conditions."}</CommandEmpty>
            {hits.length > 0 && (
              <CommandGroup heading="Conditions">
                {hits.map((hit) => (
                  <CommandItem
                    key={hit.concept_id}
                    value={String(hit.concept_id)}
                    onSelect={() => {
                      onChange([...selected, hit]);
                      setQuery("");
                    }}
                  >
                    <span className="flex-1">{hit.name}</span>
                    <span className="text-xs text-muted-foreground">
                      SNOMED {hit.code}
                      {hit.descendants > 0 && ` · +${hit.descendants} more specific`}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        )}
      </Command>
      {selected.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Selected conditions">
          {selected.map((concept) => (
            <li key={concept.concept_id}>
              <span className="inline-flex items-center gap-1 rounded-md border bg-accent py-0.5 pr-1 pl-2 text-sm text-accent-foreground">
                {concept.name}
                <button
                  type="button"
                  className="rounded p-0.5 hover:bg-card"
                  aria-label={`Remove ${concept.name}`}
                  onClick={() => onChange(selected.filter((c) => c.concept_id !== concept.concept_id))}
                >
                  <X aria-hidden className="size-3.5" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
