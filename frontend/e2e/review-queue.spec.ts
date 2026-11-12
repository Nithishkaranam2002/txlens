import { expect, test } from "@playwright/test";

import { expectNoIdentifiers, login, seed } from "./helpers";

const { review } = seed();
const itemUrl = (id: string) => `/review/biopsy_document/${id}`;

test("a clinician approves a biopsy report with the keyboard and it leaves the queue", async ({ page }) => {
  await login(page, "clinician");
  await page.goto("/review?type=biopsy_document");
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Biopsy report \d+/ })).toBeVisible();

  await page.goto(itemUrl(review.documents.approve.id));
  await expect(page.getByRole("img", { name: "Page 1 of the pathology report" })).toBeVisible();
  await page.keyboard.press("a");
  await expect(page).not.toHaveURL(new RegExp(review.documents.approve.id));

  await page.goto(itemUrl(review.documents.approve.id));
  await expect(page.getByText("This item has been decided.")).toBeVisible();
  await page.goto("/review?type=biopsy_document&status=decided");
  await expect(page.getByRole("cell", { name: /Approve/ }).first()).toBeVisible();
});

test("a clinician edits a field and must give a reason", async ({ page }) => {
  await login(page, "clinician");
  await page.goto(itemUrl(review.documents.edit.id));
  const field = page.getByRole("textbox", { name: "banff t" });
  await field.fill("3");
  await expect(page.getByRole("radio", { name: "Edit" })).toBeChecked();
  await page.getByRole("button", { name: "Save edit" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Choose a reason" })).toHaveText(
    "Choose a reason for this decision.",
  );

  await page.getByLabel("Reason (required)").selectOption("ocr_error");
  await page.getByRole("button", { name: "Save edit" }).click();
  await expect(page).not.toHaveURL(new RegExp(review.documents.edit.id));

  await page.goto(itemUrl(review.documents.edit.id));
  await expect(page.getByRole("textbox", { name: "banff t" })).toHaveValue("3");
  await expect(page.getByText("Model: 2").first()).toBeVisible();
});

test("a clinician approves an AI-curated mapping for every center", async ({ page }) => {
  await login(page, "clinician");
  await page.goto(`/review/curated_mapping/${encodeURIComponent(review.curatedKey)}`);
  await expect(page.getByText("AI-curated, pending", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page).not.toHaveURL(/curated_mapping\/TXLENS/);

  await page.goto(`/review/curated_mapping/${encodeURIComponent(review.curatedKey)}`);
  await expect(page.getByText("clinician-reviewed", { exact: true })).toBeVisible();
  const history = page.getByRole("table", { name: /Review history/ });
  await expect(history).toContainText("approve");
  await expect(history).toContainText("center_a");
});

test("a data steward sees redacted evidence only", async ({ page }) => {
  const consoleLines: string[] = [];
  page.on("console", (message) => consoleLines.push(message.text()));
  await login(page, "data_steward");
  await page.goto(itemUrl(review.documents.steward.id));
  await expect(page.getByText("De-identified view: the lines each value was read from")).toBeVisible();
  await expect(page.getByRole("img", { name: /Page \d of the pathology report/ })).toHaveCount(0);
  await expect(page.getByRole("img", { name: "Redacted line for diagnosis" })).toBeVisible();
  await expectNoIdentifiers(page, consoleLines);
  const page1 = await page.request.get(`/api/documents/${review.documents.steward.id}/pages/1`);
  expect(page1.status()).toBe(403);

  await page.goto(`/review/curated_mapping/${encodeURIComponent(review.curatedKey)}`);
  await expect(page.getByText(/Only clinicians review AI-curated mappings/)).toBeVisible();
});

test("a researcher has no access to the review queue", async ({ page }) => {
  await login(page, "researcher");
  await expect(page.getByRole("link", { name: "Review queue" })).toHaveCount(0);
  await page.goto("/review");
  await expect(page.getByRole("heading", { name: "Not available for your role" })).toBeVisible();
  const response = await page.request.get("/api/review/items");
  expect(response.status()).toBe(403);
});
