import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { adminApi } from "../../api/endpoints";
import { ErrorBox, Loading, Pagination, StatusBadge } from "../../components/common";
import { formatDate, formatMoney, statusLabel } from "../../utils/format";
import { useAsync } from "../../utils/useAsync";

const STATUSES = ["PENDING_PAYMENT", "PAID", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"];

export default function AdminOrders() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const page = Number(params.get("page") ?? "1") || 1;
  const [search, setSearch] = useState(q);
  const list = useAsync(() => adminApi.orders({ status, q, page, page_size: 20 }), [status, q, page]);

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!("page" in changes)) next.delete("page");
    setParams(next);
  };

  return (
    <>
      <h1>Orders</h1>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); update({ q: search.trim() }); }}>
        <input type="search" placeholder="Order number or customer e-mail" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={status} onChange={(e) => update({ status: e.target.value })} aria-label="Status filter">
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
        </select>
        <button className="btn btn-sm" type="submit">Search</button>
      </form>
      {list.loading ? <Loading /> : list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : (
        <div className="card">
          <table className="table">
            <thead><tr><th>Order</th><th>Customer</th><th>Date</th><th>Items</th><th>Total</th><th>Status</th></tr></thead>
            <tbody>
              {list.data?.items.map((o) => (
                <tr key={o.id}>
                  <td><Link to={`/admin/orders/${o.id}`}>{o.order_number}</Link></td>
                  <td>{o.customer.full_name}<div className="muted small">{o.customer.email}</div></td>
                  <td>{formatDate(o.created_at)}</td>
                  <td>{o.item_count}</td>
                  <td>{formatMoney(o.total, o.currency)}</td>
                  <td><StatusBadge status={o.status} /></td>
                </tr>
              ))}
              {list.data?.items.length === 0 && <tr><td colSpan={6} className="muted">No orders found.</td></tr>}
            </tbody>
          </table>
          {list.data && <Pagination page={list.data.page} pages={list.data.pages} onChange={(p) => update({ page: String(p) })} />}
        </div>
      )}
    </>
  );
}
