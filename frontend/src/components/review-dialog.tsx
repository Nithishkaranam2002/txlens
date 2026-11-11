"use client";

import { useState } from "react";

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
import { useReviewFlag, type ReviewAction } from "@/lib/queries";
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

/** A review button that opens a dialog with an optional note (the dialog traps focus). */
export function ReviewDialog({ flag, action, subject }: { flag: Flag; action: ReviewAction; subject: string }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const review = useReviewFlag();
  const text = TEXT[action];

  const submit = () => {
    review.mutate({ flagId: flag.flag_id, action, note: note.trim() || undefined });
    setOpen(false);
    setNote("");
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant={action === "resolve" ? "outline" : "secondary"} aria-label={`${text.button}: ${subject}`}>
          {text.button}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{text.title}</DialogTitle>
          <DialogDescription>{text.description}</DialogDescription>
        </DialogHeader>
        <p className="rounded-md bg-muted p-3 text-sm">{flag.reason}</p>
        <div className="space-y-2">
          <Label htmlFor={`note-${flag.flag_id}`}>Note (optional)</Label>
          <Textarea
            id={`note-${flag.flag_id}`}
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
          <Button onClick={submit}>{text.button}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
