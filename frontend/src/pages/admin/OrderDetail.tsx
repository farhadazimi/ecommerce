import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorMessage } from "../../api/client";
import { adminApi, orderApi } from "../../api/endpoints";
import { ErrorBox, Loading, Notice } from "../../components/common";
import { NEXT_STATUSES, statusLabel } from "../../utils/format";
import { useAsync } from "../../utils/useAsync";
import { OrderView } from "../OrderView";

export default function AdminOrderDetail() {
  const { id } = useParams();
  const order = useAsync(() => adminApi.order(id ?? ""), [id]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  if (order.loading) return <Loading />;
  if (order.error || !order.data) return <><ErrorBox message={order.error ?? "Order not found"} /><Link to="/admin/orders">← Orders</Link></>;
  const o = order.data;
  const next = NEXT_STATUSES[o.status] ?? [];

  const change = async (status: string) => {
    if (status === "CANCELLED" && !window.confirm("Cancel this order? Stock will be returned and payments refunded.")) return;
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      order.setData(await adminApi.setOrderStatus(o.id, status, note.trim()));
      setNote("");
      setMsg(`Order status changed to ${statusLabel(status)}.`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const invoice = async () => {
    try {
      window.open((await orderApi.invoice(o.id)).url, "_blank", "noopener");
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorBox message={error} />
      <OrderView
        order={o}
        actions={
          <>
            <Link to="/admin/orders" className="btn btn-ghost btn-sm">← Orders</Link>
            {next.length > 0 && (
              <>
                <input className="note-input" placeholder="Note (optional)" maxLength={255} value={note} onChange={(e) => setNote(e.target.value)} />
                {next.map((s) => (
                  <button key={s} type="button" className={s === "CANCELLED" ? "btn btn-sm btn-danger" : "btn btn-sm btn-primary"}
                    disabled={busy} onClick={() => change(s)}>
                    Mark {statusLabel(s)}
                  </button>
                ))}
              </>
            )}
            {o.invoice.available && <button type="button" className="btn btn-sm" onClick={invoice}>Invoice PDF</button>}
          </>
        }
      />
    </>
  );
}
