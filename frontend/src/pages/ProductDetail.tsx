import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { cartApi, catalogApi } from "../api/endpoints";
import { ErrorBox, Loading, Notice, ProductImage } from "../components/common";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import { formatMoney } from "../utils/format";
import { useAsync } from "../utils/useAsync";

export default function ProductDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const { setCart } = useCart();
  const navigate = useNavigate();
  const product = useAsync(() => catalogApi.product(id ?? ""), [id]);
  const [selected, setSelected] = useState(0);
  const [qty, setQty] = useState(1);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (product.loading) return <Loading />;
  if (product.error || !product.data) {
    return (
      <div className="container page">
        <ErrorBox message={product.error ?? "Product not found"} />
        <Link to="/products">← Back to products</Link>
      </div>
    );
  }
  const p = product.data;
  const images = p.images.length ? p.images : [];
  const current = images[selected] ?? images[0];
  const max = Math.min(p.stock.available, 99);

  const addToCart = async () => {
    if (!user) {
      navigate("/login", { state: { from: `/products/${p.id}` } });
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      setCart(await cartApi.add(p.id, qty));
      setMessage(`Added ${qty} × ${p.name} to your cart.`);
    } catch (e) {
      setError(e instanceof ApiError && e.code === "INSUFFICIENT_INVENTORY" ? e.message : errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container page">
      <nav className="breadcrumbs">
        <Link to="/products">Products</Link>
        {p.category && (
          <>
            {" / "}
            <Link to={`/category/${p.category.slug}`}>{p.category.name}</Link>
          </>
        )}
      </nav>
      <div className="product-detail">
        <div className="gallery">
          <ProductImage src={current?.url} alt={p.name} className="gallery-main" />
          {images.length > 1 && (
            <div className="thumbs">
              {images.map((img, i) => (
                <button key={img.id} type="button" className={i === selected ? "thumb active" : "thumb"} onClick={() => setSelected(i)}>
                  <img src={img.url} alt={`${p.name} ${i + 1}`} />
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="detail-info">
          <h1>{p.name}</h1>
          <p className="muted small">SKU {p.sku}</p>
          <p className="price-lg">{formatMoney(p.price, p.currency)}</p>
          {p.stock.in_stock ? (
            <p className={p.stock.low_stock ? "stock stock-low" : "stock stock-in"}>
              {p.stock.low_stock ? `Hurry — only ${p.stock.available} left` : `In stock (${p.stock.available} available)`}
            </p>
          ) : (
            <p className="stock stock-out">Out of stock</p>
          )}
          {p.description && <p className="description">{p.description}</p>}
          {p.stock.in_stock && (
            <div className="add-to-cart">
              <label>
                Quantity
                <input type="number" min={1} max={max} value={qty}
                  onChange={(e) => setQty(Math.max(1, Math.min(max, Number(e.target.value) || 1)))} />
              </label>
              <button type="button" className="btn btn-primary btn-lg" disabled={busy} onClick={addToCart}>
                {busy ? "Adding…" : "Add to cart"}
              </button>
            </div>
          )}
          {message && (
            <Notice kind="success">
              {message} <Link to="/cart">View cart →</Link>
            </Notice>
          )}
          <ErrorBox message={error} />
        </div>
      </div>
    </div>
  );
}
