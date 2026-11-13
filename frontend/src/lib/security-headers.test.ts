import { describe, expect, it } from "vitest";

import { contentSecurityPolicy, securityHeaders } from "./security-headers";

describe("securityHeaders", () => {
  it("locks down framing, plugins, base and form targets in production", () => {
    const csp = contentSecurityPolicy({ dev: false, hsts: false });
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).not.toContain("unsafe-eval");
  });

  it("allows eval only in development", () => {
    expect(contentSecurityPolicy({ dev: true, hsts: false })).toContain("'unsafe-eval'");
  });

  it("sends HSTS and upgrades requests only when served over HTTPS", () => {
    expect(securityHeaders({ dev: false, hsts: false })["Strict-Transport-Security"]).toBeUndefined();
    const https = securityHeaders({ dev: false, hsts: true });
    expect(https["Strict-Transport-Security"]).toMatch(/^max-age=\d+/);
    expect(https["Content-Security-Policy"]).toContain("upgrade-insecure-requests");
  });

  it("denies framing and MIME sniffing", () => {
    const headers = securityHeaders({ dev: false, hsts: false });
    expect(headers["X-Frame-Options"]).toBe("DENY");
    expect(headers["X-Content-Type-Options"]).toBe("nosniff");
  });
});
