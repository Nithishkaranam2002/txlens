/**
 * Frames for the README's hero GIF: worklist -> patient page -> biopsy evidence -> approve.
 * Run against a local stack restored from the demo snapshot (approving changes the data, so
 * never against the public demo). Writes PNG frames and frames.json (hold time per frame) to
 * FRAMES_DIR; infra/demo/make_gif.py turns them into docs/images/demo.gif.
 *
 * Usage: BASE_URL=http://localhost:53001 FRAMES_DIR=/tmp/frames pnpm demo-gif
 * The clinician password comes from DEMO_CLINICIAN_PASSWORD; it is never printed.
 */
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium, type Locator, type Page } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";
const FRAMES_DIR = process.env.FRAMES_DIR ?? join(__dirname, "..", "gif-frames");
const PATIENT_NAME = process.env.PATIENT_NAME ?? "Vellgate, Varek";
const DOCUMENT = process.env.REVIEW_DOCUMENT ?? "0f2a838c-cf72-4bd0-99b6-93f1b0d12a86";
const HIGHLIGHT = "3px solid #f59e0b";

/** Frames with how long each is shown; a caption bar explains each step. */
class Recorder {
  private frames: { file: string; ms: number }[] = [];

  constructor(private page: Page) {}

  async caption(text: string) {
    console.log(text);
    await this.page.evaluate((t) => {
      let bar = document.getElementById("gif-caption");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "gif-caption";
        bar.setAttribute("style", "position:fixed;left:0;right:0;bottom:0;z-index:99999;" +
          "padding:14px 24px;background:rgba(15,23,42,.88);color:#fff;font:600 22px system-ui;");
        document.body.appendChild(bar);
      }
      bar.textContent = t;
    }, text);
  }

  async hold(ms: number) {
    const file = `frame-${String(this.frames.length).padStart(4, "0")}.png`;
    await this.page.screenshot({ path: join(FRAMES_DIR, file) });
    this.frames.push({ file, ms });
  }

  /** Scroll by `pixels` in small steps, one frame per step. */
  async scroll(pixels: number, steps = 10) {
    for (let i = 0; i < steps; i++) {
      await this.page.mouse.wheel(0, pixels / steps);
      await this.page.waitForTimeout(60);
      await this.hold(90);
    }
  }

  /** Outline the element, show it, then click it. */
  async click(target: Locator, ms = 700) {
    await target.scrollIntoViewIfNeeded();
    await target.evaluate((el, outline) => { (el as HTMLElement).style.outline = outline; }, HIGHLIGHT);
    await this.hold(ms);
    await target.click();
  }

  save() {
    writeFileSync(join(FRAMES_DIR, "frames.json"), JSON.stringify(this.frames, null, 1));
    const seconds = this.frames.reduce((sum, f) => sum + f.ms, 0) / 1000;
    console.log(`${this.frames.length} frames, ${seconds.toFixed(1)} s, in ${FRAMES_DIR}`);
  }
}

async function main() {
  const password = process.env.DEMO_CLINICIAN_PASSWORD;
  if (!password) throw new Error("DEMO_CLINICIAN_PASSWORD is not set");
  rmSync(FRAMES_DIR, { recursive: true, force: true });
  mkdirSync(FRAMES_DIR, { recursive: true });
  const browser = await chromium.launch();
  const page = await (await browser.newContext({
    viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, colorScheme: "light",
  })).newPage();
  const rec = new Recorder(page);

  await page.goto(`${BASE_URL}/login`);
  await page.getByLabel("User name").fill("clinician.center_a");
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("table").waitFor();
  await page.waitForLoadState("networkidle");

  await rec.caption("1 · Worklist: patients ranked by surveillance flags, with plain-English reasons");
  await rec.hold(2600);
  await rec.click(page.getByRole("link", { name: PATIENT_NAME, exact: true }));

  await page.getByRole("heading", { name: "Results" }).waitFor();
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(600);
  await rec.caption("2 · Patient page: dd-cfDNA above both thresholds, graft function, risk model");
  await rec.hold(2400);
  const timeline = page.getByRole("heading", { name: "Clinical timeline" });
  const distance = await timeline.evaluate((el) => el.getBoundingClientRect().top - 140);
  await rec.caption("3 · The biopsy result was read by AI from the scanned report: waiting for review");
  await rec.scroll(distance, 12);
  await rec.hold(2200);

  await rec.click(page.getByRole("link", { name: "Review queue" }));
  await page.getByRole("heading", { name: "Review queue" }).waitFor();
  await page.waitForLoadState("networkidle");
  await rec.caption("4 · Review queue: low-confidence AI extractions wait for a person");
  await rec.hold(1600);
  await page.goto(`${BASE_URL}/review/biopsy_document/${DOCUMENT}`);
  await page.getByRole("img", { name: "Page 1 of the pathology report" }).waitFor();
  await page.waitForLoadState("networkidle");
  await rec.caption("5 · Evidence: boxes on the scanned page show where each value was read");
  await rec.hold(2800);

  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await page.getByRole("radio", { name: "Approve" }).check();
  await rec.caption("6 · A person approves: only checked values reach the OMOP database");
  await rec.click(approve, 1200);
  // After a decision the reviewer is taken to the next report in the queue.
  await page.waitForURL((url) => !url.pathname.endsWith(DOCUMENT));
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);
  await rec.caption("7 · Approved and audited; the next item in the queue opens");
  await rec.hold(2400);

  rec.save();
  await browser.close();
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : "demo-gif failed");
  process.exit(1);
});
