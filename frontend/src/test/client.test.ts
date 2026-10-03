import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, buildUrl, onUnauthorized, request } from "../api/client";
import { resolveApiBaseUrl } from "../config";

function mockFetch(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fn = vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : typeof body === "string" ? body : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

function expectFailure(): ApiError {
  throw new Error("expected the request to fail");
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.__APP_CONFIG__;
});

describe("config resolution", () => {
  it("prefers the runtime config and strips trailing slashes", () => {
    expect(resolveApiBaseUrl({ API_BASE_URL: "https://api.example.com/" }, "http://build")).toBe("https://api.example.com");
  });
  it("falls back to the build-time value, then same-origin", () => {
    expect(resolveApiBaseUrl(undefined, "http://build:8000")).toBe("http://build:8000");
    expect(resolveApiBaseUrl(undefined, undefined)).toBe("");
  });
  it("an explicit empty runtime value means same-origin (Kubernetes ingress)", () => {
    expect(resolveApiBaseUrl({ API_BASE_URL: "" }, "http://build")).toBe("");
  });
});

describe("api client", () => {
  it("builds URLs from the runtime base and drops empty query values", () => {
    window.__APP_CONFIG__ = { API_BASE_URL: "http://localhost:8000" };
    expect(buildUrl("/api/products", { q: "phone", page: 2, category: "", in_stock: undefined })).toBe(
      "http://localhost:8000/api/products?q=phone&page=2",
    );
  });

  it("sends credentials and JSON bodies", async () => {
    const fetchMock = mockFetch(200, { ok: true });
    await request("/api/cart/items", { method: "POST", body: { product_id: 1 }, headers: { "Idempotency-Key": "k" } });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/cart/items");
    expect(init.credentials).toBe("include");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.headers["Idempotency-Key"]).toBe("k");
    expect(init.body).toBe(JSON.stringify({ product_id: 1 }));
  });

  it("maps the backend error envelope to ApiError", async () => {
    mockFetch(409, { error: { code: "INSUFFICIENT_INVENTORY", message: "Only 2 left", details: [{ available: 2 }], request_id: "r1" } });
    const err = await request("/api/cart/items").then(expectFailure, (e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("INSUFFICIENT_INVENTORY");
    expect(err.message).toBe("Only 2 left");
    expect(err.requestId).toBe("r1");
  });

  it("handles non-JSON 5xx responses", async () => {
    mockFetch(502, "<html>Bad gateway</html>", { "Content-Type": "text/html" });
    const err = await request("/api/products").then(expectFailure, (e: ApiError) => e);
    expect(err.code).toBe("HTTP_502");
    expect(err.message).toMatch(/temporarily unavailable/);
  });

  it("reports network failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = await request("/api/products").then(expectFailure, (e: ApiError) => e);
    expect(err.code).toBe("NETWORK_ERROR");
  });

  it("notifies unauthorized listeners on 401 unless silenced", async () => {
    const listener = vi.fn();
    const off = onUnauthorized(listener);
    mockFetch(401, { error: { code: "TOKEN_EXPIRED", message: "Access token has expired" } });
    await request("/api/cart").catch(() => undefined);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener.mock.calls[0][0].code).toBe("TOKEN_EXPIRED");
    mockFetch(401, { error: { code: "UNAUTHENTICATED", message: "x" } });
    await request("/api/auth/me", { silent401: true }).catch(() => undefined);
    expect(listener).toHaveBeenCalledTimes(1);
    off();
  });

  it("returns undefined for 204 responses", async () => {
    mockFetch(204, undefined);
    await expect(request("/api/products/1", { method: "DELETE" })).resolves.toBeUndefined();
  });
});
