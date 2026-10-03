/**
 * Runtime configuration.
 *
 * Resolution order for the API base URL:
 *   1. window.__APP_CONFIG__.API_BASE_URL  (generated at container start from env vars)
 *   2. import.meta.env.VITE_API_BASE_URL   (build-time override)
 *   3. ""                                  (same origin - the Kubernetes Ingress routes /api)
 */
export interface AppConfig {
  API_BASE_URL?: string;
  APP_ENV?: string;
}

declare global {
  interface Window {
    __APP_CONFIG__?: AppConfig;
  }
}

export function resolveApiBaseUrl(
  runtime: AppConfig | undefined = typeof window !== "undefined" ? window.__APP_CONFIG__ : undefined,
  buildTime: string | undefined = import.meta.env.VITE_API_BASE_URL,
): string {
  const candidate = runtime?.API_BASE_URL ?? buildTime ?? "";
  return candidate.trim().replace(/\/+$/, "");
}

export function appEnv(): string {
  return (typeof window !== "undefined" && window.__APP_CONFIG__?.APP_ENV) || import.meta.env.MODE;
}
