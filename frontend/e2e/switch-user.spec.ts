import { expect, test, type Page } from "@playwright/test";

import { login, signIn, type Role } from "./helpers";

/**
 * Log out from the app shell and wait until every redirect after it has settled. The /login
 * page load is slowed down (as in `next dev`), which used to let a late 401 from the old
 * session redirect to /login?next=<previous page> instead.
 */
async function logOut(page: Page) {
  await page.route(
    (url) => url.pathname === "/login",
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue();
    },
  );
  await page.getByRole("button", { name: "Log out" }).click();
  await page.waitForURL((url) => url.pathname === "/login");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.unrouteAll();
}

async function expectHome(page: Page, home: RegExp, heading: string) {
  await expect(page).toHaveURL(home);
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1 })).toHaveText(heading);
  await expect(main.getByRole("heading", { name: "Not available for your role" })).toHaveCount(0);
}

const SWITCHES: { from: Role; to: Role; home: RegExp; heading: string }[] = [
  { from: "clinician", to: "admin", home: /\/admin$/, heading: "Admin" },
  { from: "data_steward", to: "researcher", home: /\/cohorts$/, heading: "Cohort builder" },
];

for (const { from, to, home, heading } of SWITCHES) {
  test(`${to} logging in after ${from} logs out lands on their own home page`, async ({ page }) => {
    await login(page, from);
    await logOut(page);
    expect(new URL(page.url()).search, "logout leaves no ?next=").toBe("");
    await signIn(page, to);
    await expectHome(page, home, heading);
  });
}

test("a ?next= page the new role may not use is ignored", async ({ page }) => {
  await page.goto("/login?next=%2Fworklist");
  await signIn(page, "admin");
  await expectHome(page, /\/admin$/, "Admin");
});
