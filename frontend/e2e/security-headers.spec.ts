import { expect, test, type Page } from "@playwright/test";

import { login, seed, type Role } from "./helpers";

/** Collects every CSP violation the browser reports on this page. */
async function watchViolations(page: Page): Promise<string[]> {
  const violations: string[] = [];
  await page.exposeFunction("reportCspViolation", (v: string) => violations.push(v));
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      (window as unknown as { reportCspViolation: (v: string) => void }).reportCspViolation(
        `${e.violatedDirective} blocked ${e.blockedURI || "inline"} (${e.sourceFile}:${e.lineNumber})`,
      );
    });
  });
  page.on("console", (m) => {
    if (m.type() === "error" && m.text().includes("Content Security Policy")) violations.push(m.text());
  });
  return violations;
}

test("pages are served with the security headers", async ({ page }) => {
  const response = await page.goto("/login");
  const headers = response!.headers();
  const csp = headers["content-security-policy"];
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).not.toContain("unsafe-eval");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(headers["permissions-policy"]).toContain("camera=()");
  expect(headers["x-powered-by"]).toBeUndefined();
});

test("API responses carry the backend's headers", async ({ request }) => {
  const response = await request.get("/api/health");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["x-frame-options"]).toBe("DENY");
  expect(response.headers()["cache-control"]).toBe("no-store");
});

const pages: [Role, string, string][] = [
  ["clinician", "/worklist", "Surveillance worklist"],
  ["clinician", `/patients/${seed().singleFlagPatient.id}`, "Results"],
  ["researcher", "/cohorts", "Cohort builder"],
  ["researcher", "/survival", "Graft survival analysis"],
  ["researcher", "/copilot", "Research copilot"],
  ["admin", "/review/metrics", "Review metrics"],
];

for (const [role, path, heading] of pages) {
  test(`${path} runs under the CSP without violations`, async ({ page }) => {
    const violations = await watchViolations(page);
    await login(page, role);
    const response = await page.goto(path);
    expect(response!.headers()["content-security-policy"]).toContain("default-src 'self'");
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(violations).toEqual([]);
  });
}
