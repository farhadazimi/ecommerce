import type { ReactNode } from "react";
import type { Order } from "../api/types";
import { StatusBadge } from "../components/common";
import { formatDate, formatMoney, statusLabel } from "../utils/format";

/** Read-only order details shared by the customer and admin pages. */
export function OrderView({ order, actions }: { order: Order; actions?: ReactNode }) {
  const cur = order.currency;
  return (
    <div className="order-view">
      <div className="card order-head">
        <div>
          <h1>Order {order.order_number}</h1>
          <p className="muted small">Placed {formatDate(order.created_at)} · {order.customer.full_name} ({order.customer.email})</p>
        </div>
        <StatusBadge status={order.status} />
      </div>
      {actions && <div className="actions">{actions}</div>}
      <div className="cart-layout">
        <div className="card">
          <h2>Items</h2>
          <table className="table">
            <thead><tr><th>Product</th><th>SKU</th><th>Qty</th><th>Unit</th><th>Total</th></tr></thead>
            <tbody>
              {order.items.map((i) => (
                <tr key={i.id}>
                  <td>{i.product_name}</td><td className="muted">{i.sku}</td><td>{i.quantity}</td>
                  <td>{formatMoney(i.unit_price, cur)}</td><td>{formatMoney(i.line_total, cur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl className="totals">
            <dt>Subtotal</dt><dd>{formatMoney(order.subtotal, cur)}</dd>
            <dt>Shipping</dt><dd>{order.shipping_fee === 0 ? "Free" : formatMoney(order.shipping_fee, cur)}</dd>
            <dt className="total">Total</dt><dd className="total">{formatMoney(order.total, cur)}</dd>
          </dl>
        </div>
        <aside>
          {order.shipping && (
            <div className="card">
              <h2>Shipping</h2>
              <address>
                {order.shipping.name}<br />
                {order.shipping.address_line1}<br />
                {order.shipping.address_line2 && <>{order.shipping.address_line2}<br /></>}
                {order.shipping.postal_code} {order.shipping.city}<br />
                {order.shipping.country}
                {order.shipping.phone && <><br />{order.shipping.phone}</>}
              </address>
              {order.notes && <p className="muted small">Notes: {order.notes}</p>}
            </div>
          )}
          {order.payments && order.payments.length > 0 && (
            <div className="card">
              <h2>Payments</h2>
              <ul className="plain">
                {order.payments.map((p) => (
                  <li key={p.id}>
                    <StatusBadge status={p.status} /> {formatMoney(p.amount, p.currency)}{" "}
                    <span className="muted small">
                      {p.payment_ref}{p.card_last4 && ` · ${p.card_brand ?? "CARD"} ****${p.card_last4}`}
                      {p.failure_reason && ` · ${p.failure_reason}`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {order.history && (
            <div className="card">
              <h2>History</h2>
              <ol className="timeline">
                {order.history.map((h, idx) => (
                  <li key={idx}>
                    <strong>{statusLabel(h.to_status)}</strong>
                    <span className="muted small"> · {formatDate(h.created_at)}</span>
                    {h.note && <div className="small">{h.note}</div>}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
