import { Link } from "react-router-dom";
import { adminApi } from "../../api/endpoints";
import { ErrorBox, Loading, StatusBadge } from "../../components/common";
import { formatDate, formatMoney, statusLabel } from "../../utils/format";
import { useAsync } from "../../utils/useAsync";

export default function Dashboard() {
  const stats = useAsync(() => adminApi.statistics(), []);
  if (stats.loading) return <Loading />;
  if (stats.error || !stats.data) return <ErrorBox message={stats.error} onRetry={stats.reload} />;
  const s = stats.data;
  const tiles: [string, string | number, string?][] = [
    ["Revenue", formatMoney(s.revenue, s.currency)],
    ["Orders", s.orders, `${s.orders_today} in the last 24h`],
    ["Products", s.products, `${s.active_products} active`],
    ["Users", s.users, `${s.customers} customers`],
    ["Categories", s.categories],
    ["Active users", s.active_users, "last 15 minutes"],
    ["Low stock", s.low_stock_products, "products"],
    ["Out of stock", s.out_of_stock_products, "products"],
  ];
  return (
    <>
      <div className="section-head">
        <h1>Dashboard</h1>
        <button type="button" className="btn btn-sm" onClick={stats.reload}>Refresh</button>
      </div>
      <div className="stat-grid">
        {tiles.map(([label, value, hint]) => (
          <div key={label} className="card stat">
            <span className="muted small">{label}</span>
            <strong>{value}</strong>
            {hint && <span className="muted small">{hint}</span>}
          </div>
        ))}
      </div>
      <div className="cart-layout">
        <div className="card">
          <h2>Recent orders</h2>
          <table className="table">
            <thead><tr><th>Order</th><th>Customer</th><th>Total</th><th>Status</th><th>Date</th></tr></thead>
            <tbody>
              {s.recent_orders.map((o) => (
                <tr key={o.id}>
                  <td><Link to={`/admin/orders/${o.id}`}>{o.order_number}</Link></td>
                  <td>{o.customer}</td>
                  <td>{formatMoney(o.total, s.currency)}</td>
                  <td><StatusBadge status={o.status} /></td>
                  <td>{formatDate(o.created_at)}</td>
                </tr>
              ))}
              {s.recent_orders.length === 0 && <tr><td colSpan={5} className="muted">No orders yet.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Orders by status</h2>
          <ul className="plain status-list">
            {Object.entries(s.orders_by_status).map(([status, n]) => (
              <li key={status}>
                <Link to={`/admin/orders?status=${status}`}>{statusLabel(status)}</Link>
                <strong>{n}</strong>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}
