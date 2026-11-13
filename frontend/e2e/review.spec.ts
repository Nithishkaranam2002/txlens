import { expect, test } from "@playwright/test";

import { login, seed } from "./helpers";

test("a clinician acknowledges, then resolves a flag", async ({ page }) => {
  const { singleFlagPatient: patient } = seed();
  await login(page, "clinician");
  await page.goto("/worklist?severity=all"); // the patient may have only a low flag
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: patient.name, exact: true }) });

  await row.getByRole("button", { name: `Acknowledge: ${patient.name}` }).click();
  const dialog = page.getByRole("dialog", { name: "Acknowledge flag" });
  await dialog.getByLabel("Note (optional)").fill("Repeat test ordered");
  await dialog.getByRole("button", { name: "Acknowledge" }).click();
  await expect(row.getByText("Acknowledged", { exact: true })).toBeVisible();
  await expect(row.getByRole("button", { name: /Acknowledge/ })).toHaveCount(0);

  await row.getByRole("button", { name: `Resolve: ${patient.name}` }).click();
  await page.getByRole("dialog", { name: "Resolve flag" }).getByRole("button", { name: "Resolve" }).click();
  await expect(row).toHaveCount(0); // its only flag is resolved, so the patient leaves the list
  await page.reload();
  await expect(page.getByRole("link", { name: patient.name, exact: true })).toHaveCount(0);

  await page.goto(`/patients/${patient.id}`);
  const history = page.getByRole("list", { name: "Status history" }).first();
  await expect(history).toContainText("Raised by the surveillance run");
  await expect(history).toContainText("Acknowledged by Dr. Imra Vellacott");
  await expect(history).toContainText("“Repeat test ordered”");
  await expect(history).toContainText("Resolved by Dr. Imra Vellacott");
});

test("a clinician acknowledges all new flags of a patient at once", async ({ page }) => {
  const { multiFlagPatient: patient } = seed();
  await login(page, "clinician");
  await page.goto("/worklist?severity=all");
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: patient.name, exact: true }) });
  await row.getByRole("button", { name: `Acknowledge all ${patient.flags} new flags for ${patient.name}` }).click();
  const dialog = page.getByRole("dialog", { name: "Acknowledge all new flags" });
  await expect(dialog.getByRole("listitem")).toHaveCount(patient.flags);
  await dialog.getByLabel("Note (optional)").fill("Clinic call booked");
  await dialog.getByRole("button", { name: "Acknowledge all" }).click();
  await expect(row.getByText("Acknowledged", { exact: true })).toHaveCount(patient.flags);
  await expect(row.getByRole("button", { name: /Acknowledge/ })).toHaveCount(0);
  await page.reload();
  await expect(row.getByText("Acknowledged", { exact: true })).toHaveCount(patient.flags);
});

test("the worklist shows the data date once and keeps low-priority patients collapsed", async ({ page }) => {
  await login(page, "clinician");
  await page.goto("/worklist");
  await expect(page.getByText(/^Data as of/)).toHaveCount(1);
  await expect(page.getByText(/\(as of /)).toHaveCount(0); // no per-flag repeat of the same date
  const low = page.locator("details").filter({ hasText: "Low priority" });
  await expect(low).not.toHaveAttribute("open");
  await expect(low.getByRole("table")).toBeHidden();
  await low.locator("summary").focus(); // keyboard: the summary opens and closes it
  await page.keyboard.press("Enter");
  await expect(low).toHaveAttribute("open");
  await expect(low.getByRole("table")).toBeVisible();
});
