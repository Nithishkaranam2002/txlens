import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PatientHeader } from "./patient-header";

const base = { sex: "female", organ: "kidney" as const, donor_type: "living" as const, status: "active" as const,
  transplant_date: "2024-01-10", death_date: null };

describe("PatientHeader", () => {
  it("shows name, MRN and birth date for clinicians", () => {
    render(<PatientHeader patient={{ ...base, patient_id: 7, mrn: "A100", given_name: "Ada", family_name: "Vale", birth_date: "1970-02-03" }} />);
    expect(screen.getByRole("heading", { name: "Vale, Ada" })).toBeInTheDocument();
    expect(screen.getByText("A100")).toBeInTheDocument();
    expect(screen.getByText("3 Feb 1970")).toBeInTheDocument();
  });

  it("shows only the pseudonym and age band for de-identified roles", () => {
    const { container } = render(
      <PatientHeader patient={{ ...base, patient_id: "R-00ff", age_band: "50-54", status: "deceased", death_date: "2025-01-02" }} />,
    );
    expect(screen.getByRole("heading", { name: "R-00ff" })).toBeInTheDocument();
    expect(screen.getByText("50-54")).toBeInTheDocument();
    expect(screen.getByText(/dates are shifted/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent(/MRN|Born/);
    expect(screen.getByText("Deceased")).toBeInTheDocument();
  });
});
