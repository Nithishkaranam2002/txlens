import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewDetail } from "@/lib/review";
import { jsonResponse, renderWithQuery } from "@/test/render";

import { DecisionForm } from "./decision-form";

const detail = (overrides: Partial<ReviewDetail> = {}): ReviewDetail => ({
  item: {
    item_type: "biopsy_document", item_key: "d1", title: "Kidney biopsy report: TCMR", detail: null,
    patient_id: "R-1", queued_at: "2026-11-12T00:00:00Z", tier: 0, tier_label: "rejection / high severity",
    rejection_related: true, high_severity: false, qc_sample: false, status: "pending", decided_at: null,
  },
  fields: [
    { name: "banff_t", label: "banff t", model_value: "2", value: "2", confidence: 0.6, choices: null, concept: false },
    { name: "diagnosis", label: "diagnosis", model_value: "TCMR", value: "TCMR", confidence: 0.9, choices: null, concept: false },
  ],
  evidence: { kind: "sentence", view: "deidentified", sentence: "x", highlight_start: 0, highlight_end: 1, section: null, note_date: null },
  actions: ["approve", "edit", "reject"],
  reason_codes: ["ocr_error", "wrong_value", "other"],
  ...overrides,
});

function renderForm(d: ReviewDetail, onDone = vi.fn()) {
  renderWithQuery(<DecisionForm detail={d} onDone={onDone} onNext={vi.fn()} onPrevious={vi.fn()} onFocusField={vi.fn()} />, "clinician");
  return onDone;
}

afterEach(() => vi.unstubAllGlobals());

describe("DecisionForm", () => {
  it("an edit needs a reason before anything is sent", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    renderForm(detail());
    const field = screen.getByRole("textbox", { name: /banff t/i });
    await userEvent.clear(field);
    await userEvent.type(field, "3");
    expect(screen.getByRole("radio", { name: "Edit" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Save edit" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a reason");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sends only the changed field with its reason", async () => {
    const fetch = vi.fn<(request: Request) => Promise<Response>>(async () => jsonResponse(200, { item_type: "biopsy_document", item_key: "d1", status: "submitted" }));
    vi.stubGlobal("fetch", fetch);
    const onDone = renderForm(detail());
    const field = screen.getByRole("textbox", { name: /banff t/i });
    await userEvent.clear(field);
    await userEvent.type(field, "3");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /reason/i }), "ocr_error");
    await userEvent.click(screen.getByRole("button", { name: "Save edit" }));
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    const request = fetch.mock.calls[0][0];
    expect(new URL(request.url).pathname).toBe("/api/review/items/biopsy_document/d1");
    expect(await request.json()).toEqual({ action: "edit", reason_code: "ocr_error", note: null, changes: { banff_t: "3" } });
  });

  it("'a' approves without a reason", async () => {
    const fetch = vi.fn<(request: Request) => Promise<Response>>(async () => jsonResponse(200, { item_type: "biopsy_document", item_key: "d1", status: "applied" }));
    vi.stubGlobal("fetch", fetch);
    const onDone = renderForm(detail());
    await userEvent.keyboard("a");
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(await fetch.mock.calls[0][0].json()).toMatchObject({ action: "approve", changes: {} });
  });

  it("is read-only when the role may not decide", () => {
    renderForm(detail({ actions: [] }));
    expect(screen.getByRole("note")).toHaveTextContent("Only clinicians review AI-curated mappings");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByRole("textbox", { name: /banff t/i })).toBeDisabled();
  });
});
