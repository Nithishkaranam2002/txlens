import type { NextConfig } from "next";

/** Where FastAPI listens. Server-only: the browser only ever talks to this Next.js origin. */
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // The container image (infra/frontend.Dockerfile) runs the self-contained server.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  typedRoutes: true,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // Same-origin API: /api/* is proxied to FastAPI, so httpOnly SameSite=Strict cookies are
  // first-party and the browser never makes a cross-origin (CORS) request.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }];
  },
};

export default nextConfig;
