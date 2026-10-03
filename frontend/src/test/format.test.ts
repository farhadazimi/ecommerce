import { describe, expect, it } from "vitest";
import { formatDate, formatMoney, NEXT_STATUSES, newIdempotencyKey, statusLabel } from "../utils/format";

describe("formatting", () => {
  it("formats money with the currency", () => {
    expect(formatMoney(1234.5, "USD")).toBe("$1,234.50");
    expect(formatMoney(10, "EUR")).toBe("€10.00");
  });
  it("tolerates unknown currencies and bad numbers", () => {
    expect(formatMoney(5, "NOT_A_CURRENCY")).toBe("5.00");
    expect(formatMoney(Number.NaN, "USD")).toBe("$0.00");
  });
  it("labels statuses", () => {
    expect(statusLabel("PENDING_PAYMENT")).toBe("Pending Payment");
  });
  it("treats naive backend timestamps as UTC", () => {
    expect(formatDate("2026-01-02T03:04:05")).toBe(new Date("2026-01-02T03:04:05Z").toLocaleString());
    expect(formatDate(null)).toBe("-");
  });
  it("generates idempotency keys accepted by the API", () => {
    const key = newIdempotencyKey();
    expect(key).toMatch(/^[A-Za-z0-9\-_]{8,64}$/);
    expect(newIdempotencyKey()).not.toBe(key);
  });
  it("mirrors the order state machine", () => {
    expect(NEXT_STATUSES.PAID).toEqual(["PROCESSING", "CANCELLED"]);
    expect(NEXT_STATUSES.DELIVERED).toEqual([]);
  });
});
