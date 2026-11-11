/** An API failure the UI can show: status, a readable message and the request id. */
export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, message: string, requestId: string | null, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.retryAfterSeconds = retryAfter;
  }
}

type ValidationItem = { msg?: string; loc?: (string | number)[] };

/** FastAPI's `detail` (a string, or a list of validation errors) as one sentence. */
export function detailMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = (detail as ValidationItem[])
      .map((item) => {
        const field = item.loc?.filter((part) => part !== "body").join(".");
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return fallback;
}

/** An ApiError from a failed response and its parsed body. */
export function toApiError(response: Response, body: unknown): ApiError {
  const retryAfter = Number(response.headers.get("Retry-After"));
  return new ApiError(
    response.status,
    detailMessage(body, response.statusText || "Request failed"),
    response.headers.get("X-Request-ID"),
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
  );
}

export function isApiError(error: unknown, status?: number): error is ApiError {
  return error instanceof ApiError && (status === undefined || error.status === status);
}
