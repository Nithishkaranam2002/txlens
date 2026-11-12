import { expect, test, type Page } from "@playwright/test";

import { expectNoIdentifiers, login } from "./helpers";

/** The e2e API answers with a keyword-scripted model (backend/tests/copilot_testutils.py). */
async function ask(page: Page, question: string) {
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Ask" }).click();
}

const log = (page: Page) => page.getByRole("log", { name: "Conversation" });

test("a researcher asks data and literature questions and sees tools, charts and sources", async ({ page }) => {
  const consoleLines: string[] = [];
  page.on("console", (message) => consoleLines.push(message.text()));
  await login(page, "researcher");
  await page.getByRole("link", { name: "Copilot" }).click();
  await expect(page.getByRole("heading", { name: "Research copilot", level: 1 })).toBeVisible();

  await ask(page, "How many kidney recipients are there?");
  await expect(log(page).getByText("The cohort tool counted the kidney recipients shown above.")).toBeVisible();
  await expect(log(page).getByRole("list", { name: "Steps the copilot took" })).toHaveText("built cohort");
  await expect(log(page).getByRole("figure").first()).toBeVisible(); // the attrition chart
  await expect(page).toHaveURL(/\/copilot\?c=[0-9a-f-]{36}$/);

  await ask(page, "Patients by organ, as a table");
  await expect(log(page).getByText("The table above counts patients by organ.")).toBeVisible();
  await log(page).getByText("Show SQL").last().click();
  await expect(log(page).getByText(/count\(DISTINCT patient\.person_id\)/i)).toBeVisible();
  await expect(log(page).getByRole("cell", { name: "kidney", exact: true })).toBeVisible();

  await ask(page, "What does the literature say about dd-cfDNA and rejection?");
  const sources = log(page).getByRole("region", { name: "Sources" });
  await expect(sources.getByRole("link", { name: /PMID 90000001/ })).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/90000001/");
  await expect(log(page).getByRole("link", { name: "Source PMID 90000001" })).toBeVisible();

  await ask(page, "List the MRNs of the kidney recipients");
  await expect(log(page).getByText(/can't share patient identifiers/)).toBeVisible();

  await page.reload(); // the conversation is stored
  await expect(log(page).getByRole("article")).toHaveCount(4);
  await expectNoIdentifiers(page, consoleLines);
});

test("data stewards have no copilot", async ({ page }) => {
  await login(page, "data_steward");
  await expect(page.getByRole("link", { name: "Copilot" })).toHaveCount(0);
  await page.goto("/copilot");
  await expect(page.getByRole("heading", { name: "Research copilot" })).toHaveCount(0);
});
