import { expect, test } from "@playwright/test";

import { expectNoIdentifiers, identifierPattern, login, seed } from "./helpers";

test("clinician sees patient names and MRNs", async ({ page }) => {
  await login(page, "clinician");
  await expect(page.getByRole("heading", { name: "Surveillance worklist" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveText(["Worklist", "Cohorts"]);
  await expect(page.getByRole("table")).toBeVisible();
  const text = await page.getByRole("main").innerText();
  expect(text).toMatch(identifierPattern());
  expect(text).toContain("MRN ");
});

test("researcher sees pseudonyms only, nowhere a name", async ({ page }) => {
  const consoleLines: string[] = [];
  page.on("console", (message) => consoleLines.push(message.text()));
  await login(page, "researcher");
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveText(["Cohorts"]);

  await page.getByLabel("Organ").click();
  await page.getByRole("option", { name: "Kidney" }).click();
  await page.getByRole("button", { name: "Preview cohort" }).click();
  await expect(page.getByText(/Cohort size:/)).toBeVisible();
  await page.getByLabel("Cohort name").fill("E2E kidney cohort");
  await page.getByRole("button", { name: "Save cohort" }).click();
  await page.getByRole("link", { name: /Open “E2E kidney cohort”/ }).click();
  await expect(page.getByRole("heading", { name: "Members" })).toBeVisible();
  await expectNoIdentifiers(page, consoleLines);

  const member = page.getByRole("table").getByRole("link").first();
  await expect(member).toHaveText(/^R-[0-9a-f]{16}$/);
  await member.click();
  await expect(page.getByText("De-identified view: dates are shifted for privacy.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Surveillance flags" })).toHaveCount(0);
  await expect(page.getByText("Age band")).toBeVisible();
  await expectNoIdentifiers(page, consoleLines);
});

test("data steward has no cohort menu and a read-only, de-identified worklist", async ({ page }) => {
  await login(page, "data_steward");
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveText(["Worklist"]);
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("button", { name: /Acknowledge|Resolve/ })).toHaveCount(0);
  await expectNoIdentifiers(page);
  await page.goto("/cohorts");
  await expect(page.getByRole("heading", { name: "Not available for your role" })).toBeVisible();
});

test("admin sees no patient data", async ({ page }) => {
  await login(page, "admin");
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText("You have no access to patient data")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveText(["Admin"]);
  await expectNoIdentifiers(page);
  for (const path of ["/worklist", `/patients/${seed().singleFlagPatient.id}`, "/cohorts"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Not available for your role" })).toBeVisible();
    await expectNoIdentifiers(page);
  }
});

test("another center's patient is simply Not found", async ({ page }) => {
  const { otherCenterPatient } = seed();
  await login(page, "clinician");
  await page.goto(`/patients/${otherCenterPatient.id}`);
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  await page.getByRole("button", { name: "Log out" }).click();
  await page.waitForURL(/\/login/);
  await login(page, "researcher");
  await page.goto(`/patients/${otherCenterPatient.pseudonym}`);
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
});
