import { useState, type FormEvent } from "react";
import { errorMessage } from "../../api/client";
import { adminApi, catalogApi } from "../../api/endpoints";
import type { Product, ProductInput } from "../../api/types";
import { ErrorBox, Notice } from "../../components/common";
import { useAsync } from "../../utils/useAsync";
import { fieldErrors } from "../Register";

export default function ProductForm({ product, onDone }: { product: Product | null; onDone: () => void }) {
  const categories = useAsync(() => catalogApi.categories(), []);
  const [current, setCurrent] = useState<Product | null>(product);
  const [form, setForm] = useState({
    sku: product?.sku ?? "", name: product?.name ?? "", description: product?.description ?? "",
    price: product ? product.price.toFixed(2) : "", category_id: product?.category?.id ? String(product.category.id) : "",
    is_active: product?.is_active ?? true, stock_quantity: product ? String(product.stock.available) : "0", low_stock_threshold: "5",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [primary, setPrimary] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setFields({});
    setMsg(null);
    const body: ProductInput = {
      sku: form.sku.trim(), name: form.name.trim(), description: form.description.trim() || null, price: form.price,
      category_id: form.category_id ? Number(form.category_id) : null, is_active: form.is_active,
      stock_quantity: Number(form.stock_quantity), low_stock_threshold: Number(form.low_stock_threshold),
    };
    try {
      if (current) {
        // stock is managed on the Inventory page for existing products (reserved units are taken into account there)
        const { stock_quantity: _s, low_stock_threshold: _t, ...update } = body;
        setCurrent(await adminApi.updateProduct(current.id, update));
        setMsg("Product saved.");
      } else {
        setCurrent(await adminApi.createProduct(body));
        setMsg("Product created. You can now upload images.");
      }
    } catch (err) {
      setFields(fieldErrors(err));
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File | undefined) => {
    if (!file || !current) return;
    setBusy(true);
    setError(null);
    try {
      setCurrent(await adminApi.uploadImage(current.id, file, primary));
      setMsg("Image uploaded.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const deleteImage = async (imageId: number) => {
    if (!current || !window.confirm("Delete this image?")) return;
    setBusy(true);
    try {
      await adminApi.deleteImage(current.id, imageId);
      setCurrent({ ...current, images: current.images.filter((i) => i.id !== imageId) });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <>
      <div className="section-head">
        <h1>{current ? `Edit: ${current.name}` : "New product"}</h1>
        <button type="button" className="btn btn-ghost" onClick={onDone}>← Back to products</button>
      </div>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorBox message={error} />
      <div className="cart-layout">
        <form className="card form" onSubmit={submit}>
          <div className="row-2">
            <label>SKU<input required pattern="[A-Za-z0-9\-_]{2,64}" value={form.sku} onChange={set("sku")} />
              {fields.sku && <span className="field-error">{fields.sku}</span>}</label>
            <label>Price<input required type="number" min={0} step="0.01" value={form.price} onChange={set("price")} />
              {fields.price && <span className="field-error">{fields.price}</span>}</label>
          </div>
          <label>Name<input required minLength={2} maxLength={200} value={form.name} onChange={set("name")} />
            {fields.name && <span className="field-error">{fields.name}</span>}</label>
          <label>Description<textarea rows={4} maxLength={5000} value={form.description} onChange={set("description")} /></label>
          <label>
            Category
            <select value={form.category_id} onChange={set("category_id")}>
              <option value="">— none —</option>
              {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          {!current && (
            <div className="row-2">
              <label>Initial stock<input type="number" min={0} value={form.stock_quantity} onChange={set("stock_quantity")} /></label>
              <label>Low-stock threshold<input type="number" min={0} value={form.low_stock_threshold} onChange={set("low_stock_threshold")} /></label>
            </div>
          )}
          <label className="checkbox">
            <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
            Active (visible in the shop)
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>{current ? "Save changes" : "Create product"}</button>
        </form>
        <div className="card">
          <h2>Images</h2>
          {!current ? (
            <p className="muted">Save the product first, then upload images.</p>
          ) : (
            <>
              <div className="image-admin">
                {current.images.map((img) => (
                  <figure key={img.id}>
                    <img src={img.url} alt={current.name} />
                    <figcaption>
                      {img.is_primary && <span className="badge badge-paid">Primary</span>}
                      <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => deleteImage(img.id)}>Delete</button>
                    </figcaption>
                  </figure>
                ))}
                {current.images.length === 0 && <p className="muted">No images yet.</p>}
              </div>
              <label className="checkbox">
                <input type="checkbox" checked={primary} onChange={(e) => setPrimary(e.target.checked)} /> Set as primary image
              </label>
              <label className="btn btn-sm">
                {busy ? "Uploading…" : "Upload image"}
                <input type="file" hidden accept="image/png,image/jpeg,image/webp,image/gif" disabled={busy}
                  onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
              </label>
              <p className="muted small">JPEG/PNG/WebP/GIF up to 5 MB. Images are optimised and stored in object storage (OBS).</p>
            </>
          )}
        </div>
      </div>
    </>
  );
}
