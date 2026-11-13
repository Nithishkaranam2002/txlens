/**
 * Security headers for every page. Static (no per-request nonce) because the app uses Partial
 * Prerendering, which a nonce-based CSP cannot work with; see docs/SECURITY.md.
 */

export type HeaderOptions = {
  /** Development needs 'unsafe-eval' (React rebuilds server error stacks with eval). */
  dev: boolean;
  /** Only behind HTTPS: send HSTS and upgrade any http:// subresource. */
  hsts: boolean;
};

/** The Content-Security-Policy value. */
export function contentSecurityPolicy({ dev, hsts }: HeaderOptions): string {
  const directives = [
    "default-src 'self'",
    // Next.js streams the RSC payload as inline <script> tags without a nonce under PPR.
    `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
    // Plotly and Radix set inline styles.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(hsts ? ["upgrade-insecure-requests"] : []),
  ];
  return directives.join("; ");
}

/** All headers to set on a page response. */
export function securityHeaders(options: HeaderOptions): Record<string, string> {
  return {
    "Content-Security-Policy": contentSecurityPolicy(options),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    ...(options.hsts ? { "Strict-Transport-Security": "max-age=63072000; includeSubDomains" } : {}),
  };
}
