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
  ["researcher", "/copilot", "Research copilot"],
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

test("worklist with the low-priority section open and the acknowledge-all dialog open", async ({ page }) => {
  await login(page, "clinician");
  await page.goto("/worklist");
  await page.locator("details summary").filter({ hasText: "Low priority" }).click();
  await expect(page.locator("details[open]").getByRole("table")).toBeVisible();
  await expectAccessible(page);
  await page.goto("/worklist?severity=all");
  await page.getByRole("button", { name: /^Acknowledge all/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "Acknowledge all new flags" });
  await expect(dialog).toBeVisible();
  // Check contrast once the fade-in has finished, not on half-transparent text.
  await dialog.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
  await expectAccessible(page);
});

test("survival results (charts, risk table, forest plot, methods)", async ({ page }) => {
  await login(page, "clinician");
  await page.goto("/survival");
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByRole("heading", { name: "Results · synthetic data" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  // The primary button's hover shade must keep AA contrast too: check it while hovered.
  await page.getByRole("button", { name: "Run analysis" }).hover();
  await expectAccessible(page);
});
