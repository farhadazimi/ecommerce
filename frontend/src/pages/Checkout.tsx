import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { cartApi, orderApi } from "../api/endpoints";
import type { Order, ShippingInfo } from "../api/types";
import { Empty, ErrorBox, Loading } from "../components/common";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import { formatMoney, newIdempotencyKey } from "../utils/format";
import { useAsync } from "../utils/useAsync";
import { PaymentStep } from "./PaymentStep";

/** /checkout (shipping → order → payment) and /checkout/pay/:orderId (pay an existing order). */
export default function Checkout() {
  const { orderId } = useParams();
  const { user } = useAuth();
  const { refresh } = useCart();
  const existing = useAsync(() => (orderId ? orderApi.get(orderId) : Promise.resolve(null)), [orderId]);
  const cartState = useAsync(() => (orderId ? Promise.resolve(null) : cartApi.get()), [orderId]);
  // one idempotency key per checkout attempt: a double-submit or network retry cannot create two orders
  const [idempotencyKey] = useState(newIdempotencyKey);
  const [order, setOrder] = useState<Order | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const p = user?.profile;
  const [shipping, setShipping] = useState<ShippingInfo>({
    shipping_name: user?.full_name ?? "",
    shipping_phone: p?.phone ?? "",
    shipping_address_line1: p?.address_line1 ?? "",
    shipping_address_line2: p?.address_line2 ?? "",
    shipping_city: p?.city ?? "",
    shipping_postal_code: p?.postal_code ?? "",
    shipping_country: p?.country ?? "",
    notes: "",
  });

  if (existing.loading || cartState.loading) return <Loading />;
  const loadError = existing.error || cartState.error;
  if (loadError) return <div className="container page"><ErrorBox message={loadError} /></div>;

  const payable = order ?? existing.data ?? null;
  if (payable) {
    if (payable.status !== "PENDING_PAYMENT") {
      return (
        <div className="container page narrow">
          <ErrorBox message={`Order ${payable.order_number} is ${payable.status} and cannot be paid.`} />
          <Link to={`/orders/${payable.id}`}>View order →</Link>
        </div>
      );
    }
    return (
      <div className="container page narrow">
        <h1>Checkout</h1>
        <ol className="steps"><li className="done">Shipping</li><li className="done">Order placed</li><li className="active">Payment</li></ol>
        <PaymentStep order={payable} />
      </div>
    );
  }

  const cart = cartState.data;
  if (!cart || cart.items.length === 0) {
    return <div className="container page"><Empty>Your cart is empty. <Link to="/products">Continue shopping →</Link></Empty></div>;
  }

  const set = (k: keyof ShippingInfo) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setShipping({ ...shipping, [k]: e.target.value });

  const placeOrder = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const clean = Object.fromEntries(Object.entries(shipping).map(([k, v]) => [k, v === "" ? null : v])) as unknown as ShippingInfo;
      const created = await orderApi.create(clean, idempotencyKey);
      setOrder(created);
      void refresh();
    } catch (err) {
      if (err instanceof ApiError && err.code === "INSUFFICIENT_INVENTORY" && Array.isArray(err.details)) {
        const lines = (err.details as { name: string; available?: number; reason: string }[])
          .map((d) => (d.reason === "UNAVAILABLE" ? `${d.name}: no longer available` : `${d.name}: only ${d.available} left`));
        setError(`${err.message}. ${lines.join("; ")}. Please update your cart.`);
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container page">
      <h1>Checkout</h1>
      <ol className="steps"><li className="active">Shipping</li><li>Order placed</li><li>Payment</li></ol>
      <div className="cart-layout">
        <form className="card form" onSubmit={placeOrder}>
          <h2>Shipping address</h2>
          <label>Full name<input required minLength={2} maxLength={120} value={shipping.shipping_name} onChange={set("shipping_name")} /></label>
          <label>Phone<input maxLength={32} value={shipping.shipping_phone ?? ""} onChange={set("shipping_phone")} /></label>
          <label>Address<input required minLength={3} maxLength={255} value={shipping.shipping_address_line1} onChange={set("shipping_address_line1")} /></label>
          <label>Address line 2<input maxLength={255} value={shipping.shipping_address_line2 ?? ""} onChange={set("shipping_address_line2")} /></label>
          <div className="row-3">
            <label>City<input required minLength={2} maxLength={100} value={shipping.shipping_city} onChange={set("shipping_city")} /></label>
            <label>Postal code<input required minLength={2} maxLength={20} value={shipping.shipping_postal_code} onChange={set("shipping_postal_code")} /></label>
            <label>Country<input required minLength={2} maxLength={100} value={shipping.shipping_country} onChange={set("shipping_country")} /></label>
          </div>
          <label>Order notes<textarea maxLength={500} value={shipping.notes ?? ""} onChange={set("notes")} /></label>
          <ErrorBox message={error} />
          <button type="submit" className="btn btn-primary btn-lg" disabled={busy}>
            {busy ? "Placing order…" : "Confirm order & continue to payment"}
          </button>
          <Link to="/cart" className="small">← Back to cart</Link>
        </form>
        <aside className="card summary">
          <h2>Your order</h2>
          <ul className="summary-items">
            {cart.items.map((i) => (
              <li key={i.id}><span>{i.quantity} × {i.product.name}</span><span>{formatMoney(i.line_total, cart.currency)}</span></li>
            ))}
          </ul>
          <dl>
            <dt>Subtotal</dt><dd>{formatMoney(cart.subtotal, cart.currency)}</dd>
            <dt>Shipping</dt><dd>{cart.shipping_fee === 0 ? "Free" : formatMoney(cart.shipping_fee, cart.currency)}</dd>
            <dt className="total">Total</dt><dd className="total">{formatMoney(cart.total, cart.currency)}</dd>
          </dl>
        </aside>
      </div>
    </div>
  );
}
