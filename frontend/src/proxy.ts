import { NextResponse } from "next/server";

import { securityHeaders } from "@/lib/security-headers";

const HEADERS = securityHeaders({
  dev: process.env.NODE_ENV === "development",
  hsts: process.env.TXLENS_HSTS === "1",
});

/** Adds the security headers (CSP, frame, sniffing, referrer, permissions, HSTS) to pages. */
export function proxy() {
  const response = NextResponse.next();
  for (const [name, value] of Object.entries(HEADERS)) response.headers.set(name, value);
  return response;
}

export const config = {
  // Pages only: /api/* is FastAPI's (it sets its own headers); hashed static assets need none.
  matcher: ["/((?!api/|_next/static/|_next/image).*)"],
};
