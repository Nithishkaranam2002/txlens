import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, type Page } from "@playwright/test";

export type Role = "clinician" | "researcher" | "data_steward" | "admin";
export type Seed = {
  passwords: Record<Role, string>;
  singleFlagPatient: { id: number; name: string };
  otherCenterPatient: { id: number; pseudonym: string };
  names: string[];
  mrns: string[];
};

/** Written by backend/tests/e2e_seed.py when the API web server starts. */
export function seed(): Seed {
  return JSON.parse(readFileSync(join(__dirname, ".seed.json"), "utf8")) as Seed;
}

export async function login(page: Page, role: Role, tenant = "center_a") {
  await page.goto("/login");
  await page.getByLabel("User name").fill(`${role}.${tenant}`);
  await page.getByLabel("Password").fill(seed().passwords[role]);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Wait for the role's home page (not "/", which redirects), so no request is still in flight.
  await page.waitForURL((url) => !["/", "/login"].includes(url.pathname));
  await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toBeVisible();
}

/** A whole-word pattern for every patient name and MRN in the e2e database. */
export function identifierPattern(): RegExp {
  const { names, mrns } = seed();
  const escaped = [...names, ...mrns].map((v) => v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`\\b(${escaped.join("|")})\\b`);
}

/** Fails if the visible page, its URL or the given console lines contain a patient identifier. */
export async function expectNoIdentifiers(page: Page, consoleLines: string[] = []) {
  const pattern = identifierPattern();
  const text = await page.locator("body").innerText();
  expect(text.match(pattern)?.[0] ?? null, "identifier in page text").toBeNull();
  expect(page.url().match(pattern)?.[0] ?? null, "identifier in URL").toBeNull();
  expect(consoleLines.join("\n").match(pattern)?.[0] ?? null, "identifier in console").toBeNull();
}
