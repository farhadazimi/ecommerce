import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { paymentApi } from "../api/endpoints";
import type { Order } from "../api/types";
import { ErrorBox } from "../components/common";
import { formatMoney } from "../utils/format";

const TEST_CARDS = [
  ["4242 4242 4242 4242", "Payment succeeds (Visa)"],
  ["5555 5555 5555 4444", "Payment succeeds (Mastercard)"],
  ["4000 0000 0000 0002", "Declined: card declined"],
  ["4000 0000 0000 9995", "Declined: insufficient funds"],
];

/** Simulated card payment for an order in PENDING_PAYMENT. A declined attempt can be retried. */
export function PaymentStep({ order }: { order: Order }) {
  const navigate = useNavigate();
  const nextYear = new Date().getFullYear() + 3;
  const [card, setCard] = useState({
    card_number: "4242 4242 4242 4242", card_holder: order.shipping?.name ?? order.customer.full_name,
    expiry_month: 12, expiry_year: nextYear, cvv: "123",
  });
  const [paymentId, setPaymentId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [declined, setDeclined] = useState<string | null>(null);

  const pay = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setDeclined(null);
    try {
      // a new payment attempt is created for the first try and after every decline
      const id = paymentId ?? (await paymentApi.create(order.id)).id;
      setPaymentId(id);
      const result = await paymentApi.confirm(id, { ...card, card_number: card.card_number.replace(/\s+/g, "") });
      navigate(`/orders/${result.order.id}?confirmed=1`, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setDeclined(err.message);
        setPaymentId(null);
      } else {
        if (err instanceof ApiError && err.code === "PAYMENT_CLOSED") setPaymentId(null);
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card form" onSubmit={pay}>
      <h2>Payment</h2>
      <p>
        Order <strong>{order.order_number}</strong> — amount due <strong>{formatMoney(order.total, order.currency)}</strong>
      </p>
      <div className="alert alert-info small">
        <strong>Payment simulator.</strong> No real money is charged. Test cards:
        <ul>
          {TEST_CARDS.map(([n, d]) => (
            <li key={n}>
              <button type="button" className="link-btn" onClick={() => setCard({ ...card, card_number: n })}>{n}</button> — {d}
            </li>
          ))}
        </ul>
      </div>
      <label>
        Card number
        <input required inputMode="numeric" pattern="[0-9 ]{12,23}" value={card.card_number}
          onChange={(e) => setCard({ ...card, card_number: e.target.value })} autoComplete="cc-number" />
      </label>
      <label>
        Card holder
        <input required minLength={2} maxLength={120} value={card.card_holder}
          onChange={(e) => setCard({ ...card, card_holder: e.target.value })} autoComplete="cc-name" />
      </label>
      <div className="row-3">
        <label>
          Month
          <input type="number" min={1} max={12} required value={card.expiry_month}
            onChange={(e) => setCard({ ...card, expiry_month: Number(e.target.value) })} />
        </label>
        <label>
          Year
          <input type="number" min={2000} max={2100} required value={card.expiry_year}
            onChange={(e) => setCard({ ...card, expiry_year: Number(e.target.value) })} />
        </label>
        <label>
          CVV
          <input required inputMode="numeric" pattern="[0-9]{3,4}" value={card.cvv}
            onChange={(e) => setCard({ ...card, cvv: e.target.value })} autoComplete="cc-csc" />
        </label>
      </div>
      {declined && (
        <div className="alert alert-error" role="alert">
          <strong>Payment declined:</strong> {declined} Your order is still reserved — try again or use another card.
        </div>
      )}
      <ErrorBox message={error} />
      <button className="btn btn-primary btn-lg" type="submit" disabled={busy}>
        {busy ? "Processing payment…" : declined ? "Retry payment" : `Pay ${formatMoney(order.total, order.currency)}`}
      </button>
    </form>
  );
}
