import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Patient } from "@/lib/types";
import { jsonResponse, renderWithQuery } from "@/test/render";

import { PatientSearch } from "./patient-search";

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

afterEach(() => {
  vi.unstubAllGlobals();
  router.push.mockClear();
});

function patient(id: number, family: string): Patient {
  return { patient_id: id, mrn: `MRN-${id}`, given_name: "Ann", family_name: family, birth_date: "1970-01-01",
    sex: "female", organ: id === 2 ? "heart" : "kidney", donor_type: null, status: "active",
    transplant_date: "2024-01-10", death_date: null };  // prettier-ignore
}

function stubApi(status = 200) {
  const requests: URL[] = [];
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    requests.push(url);
    if (status !== 200) return jsonResponse(status, { detail: "Too many requests" });
    const q = url.searchParams.get("q");
    return jsonResponse(200, { items: q === "zz" ? [] : [patient(1, "Smith"), patient(2, "Smithson")] });
  }));
  return requests;
}

describe("PatientSearch", () => {
  it("lists matches after two characters and opens the chosen one with the keyboard", async () => {
    const requests = stubApi();
    renderWithQuery(<PatientSearch role="clinician" />);
    const box = screen.getByRole("combobox", { name: "Search patients" });
    await userEvent.type(box, "s");
    expect(box).toHaveAttribute("aria-expanded", "false");
    await userEvent.type(box, "m");
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "Smith, AnnMRN MRN-1 · Kidney",
      "Smithson, AnnMRN MRN-2 · Heart",
    ]);
    expect(requests.map((url) => url.searchParams.get("q"))).toEqual(["sm"]); // debounced
    expect(options[0]).toHaveAttribute("aria-selected", "true");

    await userEvent.keyboard("{ArrowDown}");
    expect(options[1]).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", options[1].id);
    await userEvent.keyboard("{ArrowDown}"); // wraps around
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowUp}{Enter}");
    expect(router.push).toHaveBeenCalledWith("/patients/2");
    expect(box).toHaveValue("");
  });

  it("opens a patient on click and closes on Escape", async () => {
    stubApi();
    renderWithQuery(<PatientSearch role="clinician" />);
    const box = screen.getByRole("combobox", { name: "Search patients" });
    await userEvent.type(box, "smi");
    await screen.findAllByRole("option");
    await userEvent.keyboard("{Escape}");
    expect(box).toHaveAttribute("aria-expanded", "false");
    expect(box).toHaveValue("smi");
    await userEvent.keyboard("{ArrowDown}");
    await userEvent.click(screen.getByRole("option", { name: /Smith, Ann/ }));
    expect(router.push).toHaveBeenCalledWith("/patients/1");
  });

  it('focuses on "/" but not while typing in another field', async () => {
    stubApi();
    renderWithQuery(
      <>
        <input aria-label="Other field" />
        <PatientSearch role="researcher" />
      </>,
    );
    const box = screen.getByRole("combobox", { name: "Search patients" });
    expect(box).toHaveAttribute("placeholder", expect.stringMatching(/pseudonym/));
    await userEvent.click(screen.getByLabelText("Other field"));
    await userEvent.keyboard("/");
    expect(screen.getByLabelText("Other field")).toHaveValue("/");
    await userEvent.click(document.body);
    await userEvent.keyboard("/");
    expect(box).toHaveFocus();
    expect(box).toHaveValue("");
  });

  it("says when nothing matches and when searching too fast", async () => {
    stubApi();
    const first = renderWithQuery(<PatientSearch role="clinician" />);
    await userEvent.type(screen.getByRole("combobox"), "zz");
    expect(await screen.findByText("No patients match")).toBeVisible();
    first.unmount();

    stubApi(429);
    renderWithQuery(<PatientSearch role="clinician" />);
    await userEvent.type(screen.getByRole("combobox"), "sm");
    await waitFor(() => expect(screen.getByText(/Too many searches/)).toBeVisible());
  });
});
