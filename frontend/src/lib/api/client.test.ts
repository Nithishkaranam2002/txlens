import { describe, expect, it, vi } from "vitest";

import { jsonResponse } from "@/test/render";

import { createApi, CSRF_HEADER, sessionFetch, unwrap } from "./client";
import { ApiError } from "./errors";

function recorder(responses: Response[]) {
  const seen: Request[] = [];
  const base = vi.fn(async (request: Request) => {
    seen.push(request);
    return responses.shift() ?? jsonResponse(500, {});
  });
  return { base, seen };
}

describe("sessionFetch", () => {
  it("sends cookies, and the CSRF header on POST only", async () => {
    const { base, seen } = recorder([jsonResponse(200, {}), jsonResponse(200, {})]);
    const api = createApi(sessionFetch(base));
    await api.GET("/me");
    await api.POST("/cohorts/preview", { body: {} });
    expect(seen.map((r) => r.credentials)).toEqual(["include", "include"]);
    expect(seen[0].headers.get(CSRF_HEADER)).toBeNull();
    expect(seen[1].headers.get(CSRF_HEADER)).toBe("fetch");
    expect(new URL(seen[0].url).pathname).toBe("/api/me");
  });

  it("refreshes once on a 401 and retries the request", async () => {
    const { base, seen } = recorder([
      jsonResponse(401, { detail: "Not authenticated" }),
      jsonResponse(200, {}),
      jsonResponse(200, { username: "x" }),
    ]);
    const result = await createApi(sessionFetch(base)).GET("/me");
    expect(result.data).toEqual({ username: "x" });
    expect(seen.map((r) => `${r.method} ${new URL(r.url).pathname}`)).toEqual([
      "GET /api/me",
      "POST /api/auth/refresh",
      "GET /api/me",
    ]);
    expect(seen[1].headers.get(CSRF_HEADER)).toBe("fetch");
  });

  it("gives up after a failed refresh", async () => {
    const { base, seen } = recorder([jsonResponse(401, {}), jsonResponse(401, {})]);
    const result = await createApi(sessionFetch(base)).GET("/me");
    expect(result.response.status).toBe(401);
    expect(seen).toHaveLength(2);
  });

  it("shares one refresh between parallel 401s", async () => {
    const paths: string[] = [];
    let refreshed = false;
    const base = vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      paths.push(path);
      if (path === "/api/auth/refresh") {
        await new Promise((resolve) => setTimeout(resolve, 10));
        refreshed = true;
        return jsonResponse(200, {});
      }
      return refreshed ? jsonResponse(200, {}) : jsonResponse(401, {});
    });
    const api = createApi(sessionFetch(base));
    await Promise.all([api.GET("/me"), api.GET("/worklist")]);
    expect(paths.filter((p) => p === "/api/auth/refresh")).toHaveLength(1);
  });

  it("never refreshes for the auth endpoints themselves", async () => {
    const { base, seen } = recorder([jsonResponse(401, { detail: "Invalid user name or password" })]);
    const api = createApi(sessionFetch(base));
    await api.POST("/auth/login", { body: { username: "a", password: "b" } });
    expect(seen).toHaveLength(1);
  });
});

describe("unwrap", () => {
  it("throws an ApiError with the message, status and request id", () => {
    const response = jsonResponse(404, {});
    expect(() => unwrap({ error: { detail: "Patient not found" }, response })).toThrowError(
      expect.objectContaining({ status: 404, message: "Patient not found", requestId: "req-123" }) as ApiError,
    );
  });

  it("joins validation errors and reads Retry-After", () => {
    const response = jsonResponse(429, {}, { "Retry-After": "900" });
    try {
      unwrap({ error: { detail: [{ loc: ["body", "age_min"], msg: "too small" }] }, response });
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).message).toBe("age_min: too small");
      expect((error as ApiError).retryAfterSeconds).toBe(900);
    }
    expect.assertions(3);
  });
});
