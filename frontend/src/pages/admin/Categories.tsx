import { useState, type FormEvent } from "react";
import { errorMessage } from "../../api/client";
import { adminApi, catalogApi } from "../../api/endpoints";
import type { Category } from "../../api/types";
import { ErrorBox, Loading } from "../../components/common";
import { useAsync } from "../../utils/useAsync";

export default function AdminCategories() {
  const list = useAsync(() => catalogApi.categories(), []);
  const [form, setForm] = useState({ name: "", description: "" });
  const [editing, setEditing] = useState<Category | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const guard = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      list.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body = { name: form.name.trim(), description: form.description.trim() || null };
    void guard(async () => {
      if (editing) await adminApi.updateCategory(editing.id, body);
      else await adminApi.createCategory(body);
      setForm({ name: "", description: "" });
      setEditing(null);
    });
  };

  return (
    <>
      <h1>Categories</h1>
      <ErrorBox message={error} />
      <div className="cart-layout">
        <div className="card">
          {list.loading ? <Loading /> : list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : (
            <table className="table">
              <thead><tr><th>Name</th><th>Slug</th><th>Products</th><th /></tr></thead>
              <tbody>
                {list.data?.map((c) => (
                  <tr key={c.id}>
                    <td>{c.name}<div className="muted small">{c.description}</div></td>
                    <td className="muted">{c.slug}</td>
                    <td>{c.product_count}</td>
                    <td className="nowrap">
                      <button type="button" className="btn btn-sm" onClick={() => { setEditing(c); setForm({ name: c.name, description: c.description ?? "" }); }}>Edit</button>{" "}
                      <button type="button" className="btn btn-sm btn-danger" disabled={busy}
                        onClick={() => window.confirm(`Delete category "${c.name}"?`) && void guard(() => adminApi.deleteCategory(c.id))}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <form className="card form" onSubmit={submit}>
          <h2>{editing ? `Edit ${editing.name}` : "New category"}</h2>
          <label>Name<input required minLength={2} maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Description<textarea maxLength={2000} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <button className="btn btn-primary" type="submit" disabled={busy}>{editing ? "Save" : "Create"}</button>
          {editing && <button type="button" className="btn btn-ghost" onClick={() => { setEditing(null); setForm({ name: "", description: "" }); }}>Cancel</button>}
        </form>
      </div>
    </>
  );
}
