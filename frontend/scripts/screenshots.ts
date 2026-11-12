/**
 * README screenshots of the main pages, taken with Playwright against a running stack
 * (`make api` + `make frontend`). Usage: `pnpm screenshots` (BASE_URL defaults to :3000).
 * Demo passwords come from the environment or the DEMO_*_PASSWORD lines of ../.env;
 * they are never printed.
 */
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { chromium, type Page } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";
const OUT = join(__dirname, "..", "..", "docs", "images");
const HIDE_DEV_UI = "nextjs-portal { display: none !important; }";

function demoPassword(role: string): string {
  const key = `DEMO_${role.toUpperCase()}_PASSWORD`;
  if (process.env[key]) return process.env[key];
  const envFile = join(__dirname, "..", "..", ".env");
  const line = existsSync(envFile)
    ? readFileSync(envFile, "utf8").split("\n").findLast((l) => l.startsWith(`${key}=`))
    : undefined;
  if (!line) throw new Error(`${key} is not set (in the environment or .env)`);
  return line.slice(key.length + 1).trim().replace(/^["']|["']$/g, "");
}

async function login(page: Page, role: string) {
  await page.goto(`${BASE_URL}/login`);
  await page.getByLabel("User name").fill(`${role}.center_a`);
  await page.getByLabel("Password").fill(demoPassword(role));
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !["/", "/login"].includes(url.pathname));
}

async function shot(page: Page, name: string, height = 900) {
  await page.addStyleTag({ content: HIDE_DEV_UI });
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800); // let charts finish drawing
  await page.screenshot({ path: join(OUT, `app-${name}.png`), fullPage: true, clip: { x: 0, y: 0, width: 1440, height } });
  console.log(`docs/images/app-${name}.png`);
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const context = () => browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

  const anonymous = await (await context()).newPage();
  await anonymous.goto(`${BASE_URL}/login`);
  await shot(anonymous, "login");

  const clinician = await (await context()).newPage();
  await login(clinician, "clinician");
  await clinician.getByRole("table").waitFor();
  await shot(clinician, "worklist");
  await clinician.getByRole("table").getByRole("link").first().click();
  await clinician.getByRole("heading", { name: "Results" }).waitFor();
  await shot(clinician, "patient", 1500);

  const researcher = await (await context()).newPage();
  await login(researcher, "researcher");
  await researcher.getByLabel("Organ").click();
  await researcher.getByRole("option", { name: "Kidney" }).click();
  await researcher.getByRole("button", { name: "Add lab rule" }).click();
  await researcher.getByRole("button", { name: "Preview cohort" }).click();
  await researcher.getByText(/Cohort size:/).waitFor();
  await shot(researcher, "cohort", 1000);

  await researcher.goto(`${BASE_URL}/survival`);
  await researcher.getByRole("button", { name: "Run analysis" }).click();
  await researcher.getByRole("heading", { name: "Methods" }).waitFor();
  await researcher.mouse.move(0, 0);
  await shot(researcher, "survival", 2600);

  await browser.close();
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : "screenshots failed");
  process.exit(1);
});
