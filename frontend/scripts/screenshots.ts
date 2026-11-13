/**
 * README screenshots, taken with Playwright against a running stack: the cloud demo
 * (`make cloud-screenshots`) or a local one (`pnpm screenshots`, BASE_URL defaults to :3000).
 * Usage: `pnpm screenshots [pages|copilot]` (default: pages).
 *
 * Demo passwords come from the environment (DEMO_<ROLE>_PASSWORD) or ../.env; they are never
 * printed. PATIENT_ID picks the patient page, REVIEW_DOCUMENT the biopsy report in review.
 * Screenshots are 1440 px wide, light theme, without browser chrome, and must stay < 500 KB.
 */
import { existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";
const PATIENT_ID = process.env.PATIENT_ID ?? "100000399";
const REVIEW_DOCUMENT = process.env.REVIEW_DOCUMENT ?? "0f2a838c-cf72-4bd0-99b6-93f1b0d12a86";
const OUT = join(__dirname, "..", "..", "docs", "images");
const MAX_BYTES = 500_000;
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

async function newPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "light",
  });
  return context.newPage();
}

async function login(page: Page, role: string) {
  await page.goto(`${BASE_URL}/login`);
  await page.getByLabel("User name").fill(`${role}.center_a`);
  await page.getByLabel("Password").fill(demoPassword(role));
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !["/", "/login"].includes(url.pathname));
}

/** A screenshot of the top `height` pixels of the page, checked against the size limit. */
async function shot(page: Page, name: string, height = 900) {
  await page.addStyleTag({ content: HIDE_DEV_UI });
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800); // let charts finish drawing
  await page.mouse.move(0, 0); // no hover tooltips
  const path = join(OUT, `${name}.png`);
  await page.screenshot({ path, fullPage: true, clip: { x: 0, y: 0, width: 1440, height } });
  const size = statSync(path).size;
  if (size > MAX_BYTES) throw new Error(`${name}.png is ${Math.round(size / 1000)} KB (limit 500 KB)`);
  console.log(`docs/images/${name}.png (${Math.round(size / 1000)} KB)`);
}

async function pages(browser: Browser) {
  const anonymous = await newPage(browser);
  await anonymous.goto(`${BASE_URL}/login`);
  await shot(anonymous, "login");

  const clinician = await newPage(browser);
  await login(clinician, "clinician");
  await clinician.getByRole("table").waitFor();
  await shot(clinician, "clinician-worklist");

  await clinician.goto(`${BASE_URL}/patients/${PATIENT_ID}`);
  await clinician.getByRole("heading", { name: "Results" }).waitFor();
  await shot(clinician, "clinician-patient", 2200);
  const risk = clinician
    .locator('[data-slot="card"]')
    .filter({ has: clinician.getByRole("heading", { name: "Rejection-risk model" }) });
  await risk.scrollIntoViewIfNeeded();
  await risk.screenshot({ path: join(OUT, "risk-card.png") });
  console.log("docs/images/risk-card.png");

  await clinician.goto(`${BASE_URL}/review/biopsy_document/${REVIEW_DOCUMENT}`);
  await clinician.getByRole("img", { name: "Page 1 of the pathology report" }).waitFor();
  await shot(clinician, "review-biopsy-evidence", 1100);

  await clinician.goto(`${BASE_URL}/review/metrics`);
  await clinician.getByRole("heading", { name: "Review metrics" }).waitFor();
  await shot(clinician, "review-metrics", 1100);

  const steward = await newPage(browser);
  await login(steward, "data_steward");
  await steward.goto(`${BASE_URL}/review/biopsy_document/${REVIEW_DOCUMENT}`);
  await steward.getByText("De-identified view: the lines each value was read from").waitFor();
  await shot(steward, "steward-review-redacted", 1100);

  const researcher = await newPage(browser);
  await login(researcher, "researcher");
  await researcher.goto(`${BASE_URL}/cohorts`);
  await researcher.getByLabel("Organ").click();
  await researcher.getByRole("option", { name: "Kidney" }).click();
  await researcher.getByLabel("Donor type").click();
  await researcher.getByRole("option", { name: "Living" }).click();
  await researcher.getByLabel("Transplant year from").fill("2025");
  await researcher.getByRole("button", { name: "Add lab rule" }).click();
  await researcher.getByLabel("Value").fill("1.5");
  await researcher.getByRole("button", { name: "Preview cohort" }).click();
  await researcher.getByText(/Cohort size:/).waitFor();
  // A suppressed step ("<11") is part of the story: wait for it in the attrition table.
  await researcher.getByRole("cell", { name: /^<11/ }).first().waitFor({ timeout: 10_000 });
  await shot(researcher, "researcher-cohort", 1100);

  await researcher.goto(`${BASE_URL}/survival`);
  await researcher.getByRole("button", { name: "Run analysis" }).click();
  await researcher.getByRole("heading", { name: "Methods" }).waitFor();
  await shot(researcher, "researcher-survival", 2600);

  const admin = await newPage(browser);
  await login(admin, "admin");
  await admin.getByText("You have no access to patient data").waitFor();
  await shot(admin, "admin");
}

/** Copilot answers come from the LLM cache on a server without an API key (Task 4). */
async function copilot(browser: Browser) {
  const page = await newPage(browser);
  await login(page, "researcher");
  for (const [question, name, height] of [
    ["How many kidney recipients in my center had a dd-cfDNA result of 1% or more?", "copilot-answer", 1300],
    ["List the MRNs of the patients with ABMR.", "copilot-refusal", 900],
  ] as const) {
    await page.goto(`${BASE_URL}/copilot`); // a new conversation for each screenshot
    await page.getByLabel("Your question").fill(question);
    await page.getByRole("button", { name: "Ask" }).click();
    await page.getByText(/\d+\.\d s · \$/).waitFor({ timeout: 120_000 }); // the answer is done
    await shot(page, name, height);
  }
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const mode = process.argv[2] ?? "pages";
  if (mode === "pages") await pages(browser);
  else if (mode === "copilot") await copilot(browser);
  else throw new Error(`unknown mode ${mode} (pages or copilot)`);
  await browser.close();
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : "screenshots failed");
  process.exit(1);
});
