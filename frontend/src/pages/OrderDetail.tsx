import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { errorMessage } from "../api/client";
import { orderApi } from "../api/endpoints";
import { ErrorBox, Loading, Notice } from "../components/common";
import { useAsync } from "../utils/useAsync";
import { OrderView } from "./OrderView";

export default function OrderDetail() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const order = useAsync(() => orderApi.get(id ?? ""), [id]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (order.loading) return <Loading />;
  if (order.error || !order.data) {
    return <div className="container page"><ErrorBox message={order.error ?? "Order not found"} /><Link to="/orders">← My orders</Link></div>;
  }
  const o = order.data;

  const downloadInvoice = async () => {
    setBusy(true);
    setError(null);
    try {
      const inv = await orderApi.invoice(o.id);
      window.open(inv.url, "_blank", "noopener");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm("Cancel this order? Reserved items will be released.")) return;
    setBusy(true);
    setError(null);
    try {
      order.setData(await orderApi.cancel(o.id, "Cancelled by customer"));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const paid = !["PENDING_PAYMENT", "CANCELLED"].includes(o.status);
  return (
    <div className="container page">
      {params.get("confirmed") && paid && (
        <Notice kind="success">
          <strong>Thank you! Your payment was successful and order {o.order_number} is confirmed.</strong>
          {o.invoice.number && <> Invoice {o.invoice.number} has been generated.</>}
        </Notice>
      )}
      <ErrorBox message={error} />
      <OrderView
        order={o}
        actions={
          <>
            <Link to="/orders" className="btn btn-ghost btn-sm">← All orders</Link>
            {o.status === "PENDING_PAYMENT" && (
              <>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => navigate(`/checkout/pay/${o.id}`)}>Pay now</button>
                <button type="button" className="btn btn-danger btn-sm" disabled={busy} onClick={cancel}>Cancel order</button>
              </>
            )}
            {paid && (
              <button type="button" className="btn btn-sm" disabled={busy} onClick={downloadInvoice}>
                {busy ? "Preparing…" : "Download invoice (PDF)"}
              </button>
            )}
          </>
        }
      />
    </div>
  );
}
