import { resolveApiBaseUrl } from "../config";

/** Error raised for any non-2xx response, carrying the backend error envelope. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | null;

  constructor(status: number, code: string, message: string, details?: unknown, requestId?: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId ?? null;
  }

  get isAuthError(): boolean {
    return this.status === 401;
  }
}

type Listener = (err: ApiError) => void;
const unauthorizedListeners = new Set<Listener>();

/** Subscribe to 401 responses (used by the auth context to clear the session). */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  method?: string;
  query?: Query;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Do not notify unauthorized listeners (e.g. the initial /me probe). */
  silent401?: boolean;
}

export function buildUrl(path: string, query?: Query, base: string = resolveApiBaseUrl()): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return `${base}${path}${qs ? `?${qs}` : ""}`;
}

export async function parseError(response: Response): Promise<ApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // non-JSON error (e.g. proxy/HTML page)
  }
  const err = (payload as { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } | null)?.error;
  if (err && typeof err === "object") {
    return new ApiError(response.status, err.code ?? "ERROR", err.message ?? response.statusText, err.details, err.request_id);
  }
  const fallback =
    response.status >= 500 ? "The service is temporarily unavailable. Please try again." : response.statusText || "Request failed";
  return new ApiError(response.status, `HTTP_${response.status}`, fallback, undefined, response.headers.get("x-request-id"));
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", query, body, headers = {}, signal, silent401 } = options;
  const init: RequestInit = { method, credentials: "include", signal, headers: { Accept: "application/json", ...headers } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the server. Check your connection and try again.");
  }
  if (!response.ok) {
    const error = await parseError(response);
    if (error.status === 401 && !silent401) unauthorizedListeners.forEach((l) => l(error));
    throw error;
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}
