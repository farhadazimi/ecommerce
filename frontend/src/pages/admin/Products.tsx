import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { errorMessage } from "../../api/client";
import { adminApi } from "../../api/endpoints";
import type { Product } from "../../api/types";
import { ErrorBox, Loading, Pagination, ProductImage } from "../../components/common";
import { formatMoney } from "../../utils/format";
import { useAsync } from "../../utils/useAsync";
import ProductForm from "./ProductForm";

export default function AdminProducts() {
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? "1") || 1;
  const q = params.get("q") ?? "";
  const [search, setSearch] = useState(q);
  const list = useAsync(() => adminApi.products({ q, page, page_size: 20 }), [q, page]);
  const [editing, setEditing] = useState<Product | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remove = async (p: Product) => {
    if (!window.confirm(`Delete "${p.name}"? It will disappear from the catalog and carts.`)) return;
    setError(null);
    try {
      await adminApi.deleteProduct(p.id);
      list.reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  if (editing) {
    return (
      <ProductForm
        product={editing === "new" ? null : editing}
        onDone={() => {
          setEditing(null);
          list.reload();
        }}
      />
    );
  }

  return (
    <>
      <div className="section-head">
        <h1>Products</h1>
        <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}>+ New product</button>
      </div>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParams(search ? { q: search } : {}); }}>
        <input type="search" placeholder="Search name / SKU" value={search} onChange={(e) => setSearch(e.target.value)} />
        <button className="btn btn-sm" type="submit">Search</button>
      </form>
      <ErrorBox message={error} />
      {list.loading ? (
        <Loading />
      ) : list.error ? (
        <ErrorBox message={list.error} onRetry={list.reload} />
      ) : (
        <div className="card">
          <table className="table">
            <thead><tr><th /><th>Name</th><th>SKU</th><th>Category</th><th>Price</th><th>Stock</th><th>Status</th><th /></tr></thead>
            <tbody>
              {list.data?.items.map((p) => (
                <tr key={p.id}>
                  <td><ProductImage src={p.image_url} alt={p.name} className="thumb-sm" /></td>
                  <td>{p.name}</td>
                  <td className="muted">{p.sku}</td>
                  <td>{p.category?.name ?? "-"}</td>
                  <td>{formatMoney(p.price, p.currency)}</td>
                  <td>{p.stock.available}</td>
                  <td>{p.is_active ? <span className="badge badge-paid">Active</span> : <span className="badge badge-cancelled">Inactive</span>}</td>
                  <td className="nowrap">
                    <button type="button" className="btn btn-sm" onClick={() => setEditing(p)}>Edit</button>{" "}
                    <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(p)}>Delete</button>
                  </td>
                </tr>
              ))}
              {list.data?.items.length === 0 && <tr><td colSpan={8} className="muted">No products.</td></tr>}
            </tbody>
          </table>
          {list.data && (
            <Pagination page={list.data.page} pages={list.data.pages}
              onChange={(pg) => setParams({ ...(q ? { q } : {}), page: String(pg) })} />
          )}
        </div>
      )}
    </>
  );
}
