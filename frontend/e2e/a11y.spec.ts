import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { login, seed, type Role } from "./helpers";

/** WCAG 2.1 A/AA checks; serious and critical violations fail the test. */
async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking.map((v) => `${v.id}: ${v.help} at ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

test("login page", async ({ page }) => {
  await page.goto("/login");
  await expectAccessible(page);
});

const pages: [Role, string, string][] = [
  ["clinician", "/worklist", "Surveillance worklist"],
  ["clinician", `/patients/${seed().singleFlagPatient.id}`, "Results"],
  ["researcher", "/cohorts", "Cohort builder"],
  ["researcher", "/survival", "Graft survival analysis"],
  ["admin", "/admin", "Admin"],
  ["clinician", "/review", "Review queue"],
  ["clinician", `/review/biopsy_document/${seed().review.documents.steward.id}`, "Kidney biopsy report: TCMR"],
  ["data_steward", `/review/biopsy_document/${seed().review.documents.steward.id}`, "Kidney biopsy report: TCMR"],
  ["admin", "/review/metrics", "Review metrics"],
];

for (const [role, path, heading] of pages) {
  test(`${path} as ${role}`, async ({ page }) => {
    await login(page, role);
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expectAccessible(page);
  });
}

test("the worklist can be used with the keyboard alone", async ({ page }) => {
  await login(page, "clinician");
  await page.goto("/worklist"); // a fresh page load, where a keyboard user starts
  await expect(page.getByRole("table")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  const acknowledge = page.getByRole("button", { name: /^Acknowledge:/ }).first();
  await acknowledge.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Acknowledge flag" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(acknowledge).toBeFocused(); // focus returns to the button that opened it
});

test("survival results (charts, risk table, forest plot, methods)", async ({ page }) => {
  await login(page, "clinician");
  await page.goto("/survival");
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByRole("heading", { name: "Results · synthetic data" })).toBeVisible();
  await page.mouse.move(0, 0); // the pointer off the button: axe would rate its hover shade
  await page.waitForLoadState("networkidle");
  await expectAccessible(page);
});
