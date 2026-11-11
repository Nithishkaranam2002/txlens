import { describe, expect, it } from "vitest";

import { buildRules, describeRules, EMPTY_FORM, labelStep, NEW_LAB_RULE } from "./cohort-form";

const diabetes = { concept_id: 201820, name: "Diabetes mellitus", code: "73211009", descendants: 132 };

describe("buildRules", () => {
  it("sends nothing for an empty form (all patients)", () => {
    expect(buildRules(EMPTY_FORM)).toEqual({});
  });

  it("maps every field, with whole calendar years for the transplant date", () => {
    const rules = buildRules({
      ...EMPTY_FORM, organ: "kidney", donorType: "living", ageMin: "18", ageMax: "65",
      conditions: [diabetes], labs: [NEW_LAB_RULE], yearFrom: "2020", yearTo: "2022",
    });
    expect(rules).toEqual({
      organ: "kidney", donor_type: "living", age_min: 18, age_max: 65,
      condition_concept_ids: [201820],
      lab_rules: [{ lab: "dd_cfdna", op: ">=", value: 1, days_from_transplant: [0, 365] }],
      transplant_date: { start: "2020-01-01", end: "2022-12-31" },
    });
  });

  it("drops lab rules without a numeric value and keeps a one-sided year range", () => {
    const rules = buildRules({ ...EMPTY_FORM, labs: [{ ...NEW_LAB_RULE, value: "" }], yearTo: "2021" });
    expect(rules).toEqual({ transplant_date: { end: "2021-12-31" } });
  });
});

describe("labels", () => {
  it("names concepts and labs in attrition steps", () => {
    expect(labelStep("condition in [201820] (or descendants)", [diabetes])).toBe("condition in Diabetes mellitus (or descendants)");
    expect(labelStep("dd_cfdna >= 1 on days 0-365 after transplant", [])).toBe("dd-cfDNA >= 1 on days 0-365 after transplant");
  });

  it("describes saved rules in plain English", () => {
    expect(describeRules({})).toEqual(["All patients"]);
    expect(describeRules({ organ: "heart", condition_concept_ids: [1, 2],
      lab_rules: [{ lab: "dsa_mfi", op: ">=", value: 1000, days_from_transplant: [0, 90] }] })).toEqual([
      "Organ: heart",
      "Has one of 2 conditions (including more specific ones)",
      "DSA max MFI ≥ 1000 MFI on days 0–90 after transplant",
    ]);
  });
});
