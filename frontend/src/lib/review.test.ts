import { describe, expect, it } from "vitest";

import {
  changedFields,
  decisionProblem,
  itemHref,
  keyFromSegments,
  neighbour,
  scaleBox,
  type ReviewField,
  type ReviewItem,
} from "./review";

const field = (name: string, value: string | null): ReviewField =>
  ({ name, label: name, model_value: value, value, confidence: null, choices: null, concept: false }) as ReviewField;

describe("review helpers", () => {
  it("needs a reason for everything but approve, and a note for 'other'", () => {
    expect(decisionProblem("approve", null, "")).toBeNull();
    expect(decisionProblem("edit", null, "")).toMatch(/reason/);
    expect(decisionProblem("reject", "other", "  ")).toMatch(/note/);
    expect(decisionProblem("revert", "wrong_concept", "")).toBeNull();
  });

  it("sends only the fields that changed", () => {
    const fields = [field("banff_t", "2"), field("diagnosis", "TCMR")];
    expect(changedFields(fields, { banff_t: "3", diagnosis: "TCMR" })).toEqual({ banff_t: "3" });
  });

  it("scales a box from PDF points to the shown image", () => {
    expect(scaleBox([61.2, 79.2, 122.4, 158.4], [612, 792], { width: 306, height: 396 })).toEqual({
      left: 30.6, top: 39.6, width: 30.6, height: 39.6,
    });
  });

  it("keeps keys with a slash in one route and back", () => {
    const key = "TXLENS_NOTE_ABBREV:mixed tcmr/abmr";
    const href = itemHref("curated_mapping", key);
    expect(href).toBe("/review/curated_mapping/TXLENS_NOTE_ABBREV%3Amixed%20tcmr/abmr");
    expect(keyFromSegments(href.split("/").slice(3))).toBe(key);
  });

  it("finds the next and previous item, null at the ends", () => {
    const items = ["a", "b", "c"].map((k) => ({ item_key: k }) as ReviewItem);
    expect(neighbour(items, "b", 1)?.item_key).toBe("c");
    expect(neighbour(items, "a", -1)).toBeNull();
    expect(neighbour(items, "x", 1)).toBeNull();
  });
});
