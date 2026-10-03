import { useState } from "react";
import { errorMessage } from "../../api/client";
import { adminApi } from "../../api/endpoints";
import type { InventoryRow } from "../../api/types";
import { ErrorBox, Loading, Notice, Pagination } from "../../components/common";
import { formatDate } from "../../utils/format";
import { useAsync } from "../../utils/useAsync";

function Row({ row, onSaved, onError }: { row: InventoryRow; onSaved: (r: InventoryRow) => void; onError: (m: string) => void }) {
  const [qty, setQty] = useState(String(row.quantity));
  const [adjust, setAdjust] = useState("");
  const [threshold, setThreshold] = useState(String(row.low_stock_threshold));
  const [busy, setBusy] = useState(false);

  const save = async (body: { quantity?: number; adjust?: number; low_stock_threshold?: number }) => {
    setBusy(true);
    try {
      const updated = await adminApi.updateInventory(row.product_id, body);
      setQty(String(updated.quantity));
      setAdjust("");
      setThreshold(String(updated.low_stock_threshold));
      onSaved(updated);
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr className={row.low_stock ? "row-warning" : ""}>
      <td>{row.name}<div className="muted small">{row.sku}{!row.is_active && " · inactive"}</div></td>
      <td>{row.reserved}</td>
      <td><strong>{row.available}</strong></td>
      <td>
        <div className="inline-edit">
          <input type="number" min={0} value={qty} onChange={(e) => setQty(e.target.value)} aria-label="On-hand quantity" />
          <button type="button" className="btn btn-sm" disabled={busy || qty === String(row.quantity)} onClick={() => save({ quantity: Number(qty) })}>Set</button>
        </div>
      </td>
      <td>
        <div className="inline-edit">
          <input type="number" placeholder="±" value={adjust} onChange={(e) => setAdjust(e.target.value)} aria-label="Adjust by" />
          <button type="button" className="btn btn-sm" disabled={busy || !adjust || Number(adjust) === 0} onClick={() => save({ adjust: Number(adjust) })}>Apply</button>
        </div>
      </td>
      <td>
        <div className="inline-edit">
          <input type="number" min={0} value={threshold} onChange={(e) => setThreshold(e.target.value)} aria-label="Low-stock threshold" />
          <button type="button" className="btn btn-sm" disabled={busy || threshold === String(row.low_stock_threshold)}
            onClick={() => save({ low_stock_threshold: Number(threshold) })}>Set</button>
        </div>
      </td>
      <td className="muted small">{formatDate(row.updated_at)}</td>
    </tr>
  );
}

export default function AdminInventory() {
  const [lowOnly, setLowOnly] = useState(false);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const list = useAsync(() => adminApi.inventory({ low_stock: lowOnly || undefined, q: query, page, page_size: 50 }), [lowOnly, query, page]);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  return (
    <>
      <h1>Inventory</h1>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setPage(1); setQuery(q); }}>
        <input type="search" placeholder="Search name / SKU" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn btn-sm" type="submit">Search</button>
        <label className="checkbox">
          <input type="checkbox" checked={lowOnly} onChange={(e) => { setPage(1); setLowOnly(e.target.checked); }} /> Low stock only
        </label>
      </form>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorBox message={error} />
      {list.loading ? <Loading /> : list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : (
        <div className="card">
          <p className="muted small">Available = on-hand − reserved (reserved units belong to orders awaiting payment).</p>
          <table className="table">
            <thead><tr><th>Product</th><th>Reserved</th><th>Available</th><th>On hand</th><th>Adjust</th><th>Low-stock at</th><th>Updated</th></tr></thead>
            <tbody>
              {list.data?.items.map((r) => (
                <Row key={r.product_id} row={r}
                  onSaved={(u) => { setError(null); setMsg(`Stock for ${u.name} updated.`); list.setData({ ...list.data!, items: list.data!.items.map((x) => (x.product_id === u.product_id ? u : x)) }); }}
                  onError={(m) => { setMsg(null); setError(m); }} />
              ))}
              {list.data?.items.length === 0 && <tr><td colSpan={7} className="muted">Nothing to show.</td></tr>}
            </tbody>
          </table>
          {list.data && <Pagination page={list.data.page} pages={list.data.pages} onChange={setPage} />}
        </div>
      )}
    </>
  );
}
