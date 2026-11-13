/**
 * Page load times on the full demo data: each page is opened RUNS times in a fresh navigation
 * and timed until its main heading is shown and the network is idle. Writes
 * evals/reports/perf_pages.json, which `make perf` adds to evals/reports/perf.md.
 * Usage: `BASE_URL=http://localhost:3000 pnpm perf-pages` against a production build
 * (`make demo`), not `next dev` (it compiles pages on first visit).
 * Demo passwords come from the environment or ../.env; they are never printed.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium, type Page } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";
const RUNS = Number(process.env.RUNS ?? 5);
const OUT = join(__dirname, "..", "..", "evals", "reports", "perf_pages.json");

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

/** Navigate and wait until the page's own data is on screen (`ready`), not for network idle:
 * Playwright's idle state adds a fixed 500 ms of silence to every measurement. */
async function time(page: Page, path: string, ready: (page: Page) => Promise<void>): Promise<number[]> {
  const times: number[] = [];
  for (let i = 0; i < RUNS + 1; i++) {
    const started = performance.now();
    await page.goto(`${BASE_URL}${path}`);
    await ready(page);
    if (i > 0) times.push(performance.now() - started); // the first visit warms caches
  }
  return times;
}

const tableRow = async (page: Page) => page.getByRole("row").nth(1).waitFor();
const heading = (name: string) => async (page: Page) =>
  page.getByRole("heading", { name, exact: true }).waitFor();

const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

async function main() {
  const browser = await chromium.launch();
  const pages: Record<string, { p50: number; max: number; runs: number }> = {};
  const record = (name: string, times: number[]) => {
    pages[name] = { p50: median(times), max: Math.max(...times), runs: times.length };
    console.log(`${name}: p50 ${pages[name].p50.toFixed(0)} ms`);
  };

  const clinician = await (await browser.newContext()).newPage();
  await login(clinician, "clinician");
  record("Worklist (clinician)", await time(clinician, "/worklist", tableRow));
  const patient = await clinician.getByRole("table").getByRole("link").first().getAttribute("href");
  record("Patient page", await time(clinician, patient!, heading("Results")));
  record("Review queue", await time(clinician, "/review", tableRow));

  const researcher = await (await browser.newContext()).newPage();
  await login(researcher, "researcher");
  record("Cohort builder", await time(researcher, "/cohorts", heading("Cohort builder")));
  record("Survival analysis", await time(researcher, "/survival", heading("Graft survival analysis")));

  await browser.close();
  writeFileSync(OUT, `${JSON.stringify({ base_url: BASE_URL, pages }, null, 2)}\n`);
  console.log(`wrote ${OUT}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
