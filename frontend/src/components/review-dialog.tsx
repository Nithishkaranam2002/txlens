"use client";

import { useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAcknowledgeAll, useReviewFlag, type ReviewAction } from "@/lib/queries";
import type { Flag } from "@/lib/types";

const TEXT: Record<ReviewAction, { button: string; title: string; description: string }> = {
  acknowledge: {
    button: "Acknowledge",
    title: "Acknowledge flag",
    description: "You have seen this flag and are looking into it.",
  },
  resolve: {
    button: "Resolve",
    title: "Resolve flag",
    description: "Reviewed; no further action from this flag. A resolved flag is not reopened.",
  },
};

type NoteDialogProps = {
  button: string;
  ariaLabel: string;
  title: string;
  description: string;
  confirm: string;
  variant: "outline" | "secondary";
  children: ReactNode; // what is being reviewed
  onSubmit: (note: string | undefined) => void;
};

/** A review button that opens a dialog with an optional note (the dialog traps focus). */
function NoteDialog({ button, ariaLabel, title, description, confirm, variant, children, onSubmit }: NoteDialogProps) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const noteId = useId();

  const submit = () => {
    onSubmit(note.trim() || undefined);
    setOpen(false);
    setNote("");
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant={variant} aria-label={ariaLabel}>
          {button}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {children}
        <div className="space-y-2">
          <Label htmlFor={noteId}>Note (optional)</Label>
          <Textarea
            id={noteId}
            value={note}
            maxLength={2000}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. repeat test ordered"
          />
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button onClick={submit}>{confirm}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Acknowledge or resolve one flag. */
export function ReviewDialog({ flag, action, subject }: { flag: Flag; action: ReviewAction; subject: string }) {
  const review = useReviewFlag();
  const text = TEXT[action];
  return (
    <NoteDialog
      button={text.button}
      ariaLabel={`${text.button}: ${subject}`}
      title={text.title}
      description={text.description}
      confirm={text.button}
      variant={action === "resolve" ? "outline" : "secondary"}
      onSubmit={(note) => review.mutate({ flagId: flag.flag_id, action, note })}
    >
      <p className="rounded-md bg-muted p-3 text-sm">{flag.reason}</p>
    </NoteDialog>
  );
}

/** Acknowledge every new flag of one patient, with one note for all of them. */
export function AcknowledgeAllDialog({ patientId, flags, subject }: { patientId: string; flags: Flag[]; subject: string }) {
  const acknowledge = useAcknowledgeAll();
  return (
    <NoteDialog
      button={`Acknowledge all (${flags.length})`}
      ariaLabel={`Acknowledge all ${flags.length} new flags for ${subject}`}
      title="Acknowledge all new flags"
      description={`You have seen these ${flags.length} flags for ${subject} and are looking into them.`}
      confirm="Acknowledge all"
      variant="secondary"
      onSubmit={(note) => acknowledge.mutate({ patientId, note })}
    >
      <ul className="space-y-1 rounded-md bg-muted p-3 text-sm">
        {flags.map((flag) => (
          <li key={flag.flag_id}>{flag.reason}</li>
        ))}
      </ul>
    </NoteDialog>
  );
}
