const formatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(amount: number, currency = "USD", locale = "en-US"): string {
  const key = `${locale}:${currency}`;
  let fmt = formatters.get(key);
  if (!fmt) {
    try {
      fmt = new Intl.NumberFormat(locale, { style: "currency", currency });
    } catch {
      fmt = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    formatters.set(key, fmt);
  }
  return fmt.format(Number.isFinite(amount) ? amount : 0);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  // backend timestamps are naive UTC
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export function statusLabel(status: string): string {
  return status
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Order status transitions an admin may apply (mirrors the order service state machine). */
export const NEXT_STATUSES: Record<string, string[]> = {
  PENDING_PAYMENT: ["CANCELLED"],
  PAID: ["PROCESSING", "CANCELLED"],
  PROCESSING: ["SHIPPED", "CANCELLED"],
  SHIPPED: ["DELIVERED"],
  DELIVERED: [],
  CANCELLED: [],
};
