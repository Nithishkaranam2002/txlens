import { expect, test } from "@playwright/test";

import { expectNoIdentifiers, login, seed } from "./helpers";

test("clinician finds a patient from the top bar with the keyboard", async ({ page }) => {
  const { singleFlagPatient: patient } = seed();
  const [family] = patient.name.split(", ");
  await login(page, "clinician");
  await page.getByRole("main").click(); // focus outside any field
  await page.keyboard.press("/");
  const box = page.getByRole("combobox", { name: "Search patients" });
  await expect(box).toBeFocused();
  await box.pressSequentially(family);
  const option = page.getByRole("option", { name: new RegExp(patient.name) });
  await expect(option).toBeVisible();
  await expect(option).toContainText("MRN ");
  const options = page.getByRole("option");
  expect(await options.count()).toBeLessThanOrEqual(10);

  for (let i = 0; i < 10 && (await option.getAttribute("aria-selected")) !== "true"; i++) {
    await box.press("ArrowDown");
  }
  await box.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/patients/${patient.id}$`));
  await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toContainText(family);

  await box.fill("zz-no-such-patient");
  await expect(page.getByText("No patients match")).toBeVisible();
  await box.press("Escape");
  await expect(page.getByRole("listbox")).toBeHidden();
});

test("researcher search matches pseudonyms only, never names or another center", async ({ page }) => {
  const { singleFlagPatient: patient, otherCenterPatient } = seed();
  await login(page, "researcher");
  const box = page.getByRole("combobox", { name: "Search patients" });
  await box.fill(patient.name.split(", ")[0]);
  await expect(page.getByText("No patients match")).toBeVisible();
  await box.fill(otherCenterPatient.pseudonym);
  await expect(page.getByText("No patients match")).toBeVisible();

  await box.fill("R-");
  const first = page.getByRole("option").first();
  await expect(first).toContainText(/^R-[0-9a-f]{16}/);
  await expect(page.getByRole("option")).toHaveCount(10);
  await expectNoIdentifiers(page);
  await first.click();
  await expect(page).toHaveURL(/\/patients\/R-[0-9a-f]{16}$/);
  await expectNoIdentifiers(page);
});

test("admin has no patient search", async ({ page }) => {
  await login(page, "admin");
  await expect(page.getByRole("combobox", { name: "Search patients" })).toHaveCount(0);
});
