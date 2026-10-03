import { Link } from "react-router-dom";
import type { Product } from "../api/types";
import { formatMoney } from "../utils/format";
import { ProductImage } from "./common";

export function ProductCard({ product }: { product: Product }) {
  const { stock } = product;
  return (
    <article className="product-card">
      <Link to={`/products/${product.id}`} className="product-card-link">
        <ProductImage src={product.image_url} alt={product.name} className="product-card-img" />
        <div className="product-card-body">
          {product.category && <span className="muted small">{product.category.name}</span>}
          <h3>{product.name}</h3>
          <div className="product-card-footer">
            <strong>{formatMoney(product.price, product.currency)}</strong>
            {!stock.in_stock ? (
              <span className="stock stock-out">Out of stock</span>
            ) : stock.low_stock ? (
              <span className="stock stock-low">Only {stock.available} left</span>
            ) : (
              <span className="stock stock-in">In stock</span>
            )}
          </div>
        </div>
      </Link>
    </article>
  );
}
