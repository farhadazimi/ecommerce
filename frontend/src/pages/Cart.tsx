import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { errorMessage } from "../api/client";
import { cartApi } from "../api/endpoints";
import { Empty, ErrorBox, Loading, Notice, ProductImage } from "../components/common";
import { useCart } from "../context/CartContext";
import { formatMoney } from "../utils/format";
import { useAsync } from "../utils/useAsync";

export default function CartPage() {
  const { setCart } = useCart();
  const navigate = useNavigate();
  const state = useAsync(() => cartApi.get(), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (op: () => ReturnType<typeof cartApi.get>) => {
    setBusy(true);
    setError(null);
    try {
      const cart = await op();
      state.setData(cart);
      setCart(cart);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (state.loading) return <Loading />;
  if (state.error) return <div className="container page"><ErrorBox message={state.error} onRetry={state.reload} /></div>;
  const cart = state.data!;
  const blocked = cart.items.some((i) => !i.in_stock);

  return (
    <div className="container page">
      <h1>Shopping cart</h1>
      <ErrorBox message={error} />
      {cart.items.length === 0 ? (
        <Empty>
          Your cart is empty. <Link to="/products">Start shopping →</Link>
        </Empty>
      ) : (
        <div className="cart-layout">
          <div className="card">
            {cart.warnings.map((w) => (
              <Notice key={w} kind="warning">{w}</Notice>
            ))}
            <table className="table cart-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Price</th>
                  <th>Quantity</th>
                  <th>Total</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {cart.items.map((item) => (
                  <tr key={item.id} className={item.in_stock ? "" : "row-warning"}>
                    <td>
                      <div className="cart-product">
                        <ProductImage src={item.product.image_url} alt={item.product.name} className="thumb-sm" />
                        <div>
                          <Link to={`/products/${item.product.id}`}>{item.product.name}</Link>
                          <div className="muted small">{item.product.sku} · {item.available} available</div>
                        </div>
                      </div>
                    </td>
                    <td>{formatMoney(item.unit_price, cart.currency)}</td>
                    <td>
                      <div className="qty">
                        <button type="button" className="btn btn-sm" disabled={busy || item.quantity <= 1}
                          onClick={() => run(() => cartApi.update(item.id, item.quantity - 1))} aria-label="Decrease">−</button>
                        <span>{item.quantity}</span>
                        <button type="button" className="btn btn-sm" disabled={busy || item.quantity >= Math.min(item.available, 99)}
                          onClick={() => run(() => cartApi.update(item.id, item.quantity + 1))} aria-label="Increase">+</button>
                      </div>
                    </td>
                    <td>{formatMoney(item.line_total, cart.currency)}</td>
                    <td>
                      <button type="button" className="btn btn-sm btn-ghost" disabled={busy}
                        onClick={() => run(() => cartApi.remove(item.id))}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
              onClick={() => window.confirm("Remove all items from your cart?") && run(() => cartApi.clear())}>
              Clear cart
            </button>
          </div>
          <aside className="card summary">
            <h2>Summary</h2>
            <dl>
              <dt>Items</dt><dd>{cart.item_count}</dd>
              <dt>Subtotal</dt><dd>{formatMoney(cart.subtotal, cart.currency)}</dd>
              <dt>Shipping</dt><dd>{cart.shipping_fee === 0 ? "Free" : formatMoney(cart.shipping_fee, cart.currency)}</dd>
              <dt className="total">Total</dt><dd className="total">{formatMoney(cart.total, cart.currency)}</dd>
            </dl>
            <button type="button" className="btn btn-primary btn-block" disabled={busy || blocked} onClick={() => navigate("/checkout")}>
              Proceed to checkout
            </button>
            {blocked && <p className="muted small">Adjust the highlighted items before checking out.</p>}
          </aside>
        </div>
      )}
    </div>
  );
}
