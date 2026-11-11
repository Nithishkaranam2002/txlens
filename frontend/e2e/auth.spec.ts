import { expect, test } from "@playwright/test";

import { login, seed } from "./helpers";

test("wrong password shows one clear message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("User name").fill("clinician.center_a");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Invalid user name or password.");
});

test("too many failures show the rate-limit message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("User name").fill("lockout.e2e");
  for (let attempt = 0; attempt < 6; attempt++) {
    await page.getByLabel("Password").fill(`wrong-${attempt}`);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
  }
  await expect(page.getByRole("main").getByRole("alert")).toHaveText(/Too many failed sign-in attempts\. Try again in 15 minutes\./);
});

test("signed-out visitors are sent to login and come back afterwards", async ({ page }) => {
  await page.goto("/worklist?severity=high");
  await page.waitForURL(/\/login\?next=%2Fworklist/);
  await page.getByLabel("User name").fill("clinician.center_a");
  await page.getByLabel("Password").fill(seed().passwords.clinician);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/worklist$/);
});

test("an expired access cookie is refreshed silently", async ({ page, context }) => {
  await login(page, "clinician");
  await context.clearCookies({ name: "txlens_access" });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Surveillance worklist" })).toBeVisible();
  const names = (await context.cookies()).map((c) => c.name);
  expect(names).toContain("txlens_access");
});

test("logout clears the session", async ({ page }) => {
  await login(page, "researcher");
  await page.getByRole("button", { name: "Log out" }).click();
  await page.waitForURL(/\/login/);
  await page.goto("/cohorts");
  await page.waitForURL(/\/login/);
});
