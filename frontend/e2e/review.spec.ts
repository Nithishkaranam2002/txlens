import { expect, test } from "@playwright/test";

import { login, seed } from "./helpers";

test("a clinician acknowledges, then resolves a flag", async ({ page }) => {
  const { singleFlagPatient: patient } = seed();
  await login(page, "clinician");
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
