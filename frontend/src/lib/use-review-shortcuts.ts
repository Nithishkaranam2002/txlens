"use client";

import { useEffect, useRef } from "react";

export type ShortcutHandlers = {
  approve: () => void;
  edit: () => void;
  reject: () => void;
  next: () => void;
  previous: () => void;
  help: () => void;
};

export const SHORTCUTS: readonly { key: string; does: string }[] = [
  { key: "a", does: "Approve and go to the next item" },
  { key: "e", does: "Edit: go to the first field" },
  { key: "r", does: "Reject: go to the reason" },
  { key: "j", does: "Next item" },
  { key: "k", does: "Previous item" },
  { key: "?", does: "Show these shortcuts" },
];

const ACTIONS: Record<string, keyof ShortcutHandlers> = {
  a: "approve",
  e: "edit",
  r: "reject",
  j: "next",
  k: "previous",
  "?": "help",
};

/** True while the user is typing: shortcuts must not steal letters from a field. */
function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.getAttribute("role") === "combobox";
}

/** Single-key review shortcuts, off while typing or with a modifier key held. */
export function useReviewShortcuts(handlers: ShortcutHandlers, enabled = true) {
  const latest = useRef(handlers);
  useEffect(() => {
    latest.current = handlers;
  });
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.repeat || typing(event.target)) return;
      const action = ACTIONS[event.key];
      if (!action) return;
      event.preventDefault();
      latest.current[action]();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}
