import { Link, useSearchParams } from "react-router-dom";
import { orderApi } from "../api/endpoints";
import { Empty, ErrorBox, Loading, Pagination, StatusBadge } from "../components/common";
import { formatDate, formatMoney } from "../utils/format";
import { useAsync } from "../utils/useAsync";

export default function Orders() {
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? "1") || 1;
  const orders = useAsync(() => orderApi.list({ page, page_size: 10 }), [page]);

  return (
    <div className="container page">
      <h1>My orders</h1>
      {orders.loading ? (
        <Loading />
      ) : orders.error ? (
        <ErrorBox message={orders.error} onRetry={orders.reload} />
      ) : !orders.data?.items.length ? (
        <Empty>You have not placed any orders yet. <Link to="/products">Start shopping →</Link></Empty>
      ) : (
        <>
          <div className="card">
            <table className="table">
              <thead><tr><th>Order</th><th>Date</th><th>Items</th><th>Total</th><th>Status</th></tr></thead>
              <tbody>
                {orders.data.items.map((o) => (
                  <tr key={o.id}>
                    <td><Link to={`/orders/${o.id}`}>{o.order_number}</Link></td>
                    <td>{formatDate(o.created_at)}</td>
                    <td>{o.item_count}</td>
                    <td>{formatMoney(o.total, o.currency)}</td>
                    <td><StatusBadge status={o.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={orders.data.page} pages={orders.data.pages} onChange={(p) => setParams({ page: String(p) })} />
        </>
      )}
    </div>
  );
}
