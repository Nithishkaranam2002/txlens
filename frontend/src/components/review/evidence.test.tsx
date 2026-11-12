import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentPane } from "@/lib/review";

import { DocumentEvidence, SentenceEvidence } from "./evidence";

const field = { field: "diagnosis", value: "TCMR", extracted_value: "TCMR", line_text: "- Acute TCMR",
  source_text: "Acute", page: 1, bbox: [61.2, 79.2, 122.4, 158.4], confidence: 0.9, passes_agree: true, problems: [] };
const base = { document_id: "d1", status: "in_review", organ: "kidney", confidence: 0.6, review_reasons: [], qc_sample: false };

describe("evidence panes", () => {
  it("marks the mention in the sentence", () => {
    render(<SentenceEvidence pane={{ kind: "sentence", view: "deidentified", sentence: "Hollis reports rejection today.",
      highlight_start: 15, highlight_end: 24, section: null, note_date: "2024-06-01" }} />);
    expect(screen.getByText("rejection", { selector: "mark" })).toBeInTheDocument();
    expect(screen.getByText("De-identified")).toBeInTheDocument();
  });

  it("clinicians: a box per field on the page image, scaled to its shown size", () => {
    const pane: DocumentPane = { kind: "document", crop_urls: {}, evidence: { ...base, view: "full",
      page_sizes: [[612, 792]], page_urls: ["/documents/d1/pages/1"], fields: [field] } };
    render(<DocumentEvidence pane={pane} active="diagnosis" />);
    const image = screen.getByRole("img", { name: /page 1/i });
    expect(image).toHaveAttribute("src", "/api/documents/d1/pages/1");
    Object.defineProperty(image, "clientWidth", { value: 306 });
    Object.defineProperty(image, "clientHeight", { value: 396 });
    fireEvent.load(image);
    expect(screen.getByTestId("box-diagnosis")).toHaveStyle({ left: "28.6px", top: "37.6px" });
  });

  it("data stewards: redacted lines and crops only, never a page", () => {
    const pane: DocumentPane = { kind: "document", crop_urls: { diagnosis: "/documents/d1/fields/diagnosis/crop" },
      evidence: { ...base, view: "redacted", page_sizes: [], page_urls: [], fields: [{ ...field, page: null, bbox: null }] } };
    render(<DocumentEvidence pane={pane} active={null} />);
    expect(screen.queryByRole("img", { name: /page/i })).toBeNull();
    expect(screen.getByRole("img", { name: /redacted line for diagnosis/i })).toHaveAttribute(
      "src", "/api/documents/d1/fields/diagnosis/crop");
    expect(screen.getByText("- Acute TCMR")).toBeInTheDocument();
  });
});
