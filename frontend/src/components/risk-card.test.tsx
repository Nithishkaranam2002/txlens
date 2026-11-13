import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RiskScore } from "@/lib/types";

import { RiskCard, featureLabel, featureValue } from "./risk-card";

const LABEL = "Trained on synthetic data, not clinically validated, decision support only.";

const scored: RiskScore = {
  available: true,
  label: LABEL,
  score: 0.123,
  band: "elevated",
  threshold: 0.05,
  visit_date: "2026-08-01",
  scored_at: "2026-09-30T02:00:00Z",
  source: "nightly",
  model_name: "txlens-rejection-risk",
  model_version: "2",
  contributions: [
    { feature: "dd_cfdna_log", label: "dd-cfDNA (log)", value: Math.log(1.5), log_odds: 1.2, direction: "raises" },
    { feature: "months_since_transplant", label: "Time since transplant", value: 14.2, log_odds: -0.6, direction: "lowers" },
    { feature: "dsa_positive", label: "DSA positive", value: 1, log_odds: 0.3, direction: "raises" },
  ],
};

describe("RiskCard", () => {
  it("shows the score, band, top features, model version and the label", () => {
    render(<RiskCard risk={scored} canRescore={false} />);
    expect(screen.getByRole("heading", { name: "Rejection-risk model" })).toBeVisible();
    expect(screen.getByText(LABEL)).toBeVisible();
    expect(screen.getByText("12%")).toBeVisible();
    expect(screen.getByText("Elevated model risk")).toBeVisible();
    const features = screen.getByRole("list", { name: "Top contributing features" });
    expect(features).toHaveTextContent("dd-cfDNA1.50%raises risk");
    expect(features).toHaveTextContent("Time since transplant14 monthslowers risk");
    expect(features).toHaveTextContent("DSA positiveyesraises risk");
    expect(screen.getByText(/Model txlens-rejection-risk v2/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /Re-score/ })).toBeNull();
  });

  it("never words the score as a diagnosis", () => {
    const { container } = render(<RiskCard risk={scored} canRescore />);
    expect(container.textContent).not.toMatch(/diagnos|rejection detected|has rejection|confirmed rejection/i);
  });

  it("says when a patient has not been scored, still with the label", () => {
    render(<RiskCard risk={{ available: false, label: LABEL, contributions: [] }} canRescore={false} />);
    expect(screen.getByText(/Not scored yet/)).toBeVisible();
    expect(screen.getByText(LABEL)).toBeVisible();
  });

  it("lets clinicians re-score the latest visit", async () => {
    const onRescore = vi.fn();
    render(<RiskCard risk={scored} canRescore onRescore={onRescore} />);
    await userEvent.click(screen.getByRole("button", { name: "Re-score latest visit" }));
    expect(onRescore).toHaveBeenCalledOnce();
  });
});

describe("feature display", () => {
  it("shows values in clinical units", () => {
    expect(featureValue({ feature: "dd_cfdna_slope", value: 0.214 })).toBe("+0.21% per month");
    expect(featureValue({ feature: "creatinine_change_pct", value: -12.4 })).toBe("-12% vs baseline");
    expect(featureValue({ feature: "tacrolimus_cv", value: 21.6 })).toBe("CV 22%");
    expect(featureValue({ feature: "dgf", value: 0 })).toBe("no");
    expect(featureValue({ feature: "dd_cfdna_vs_min", value: null })).toBe("not available");
    expect(featureLabel({ feature: "dd_cfdna_log", label: "dd-cfDNA (log)" })).toBe("dd-cfDNA");
  });
});
