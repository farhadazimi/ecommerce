import { Link } from "react-router-dom";
import { catalogApi } from "../api/endpoints";
import { ErrorBox, Loading } from "../components/common";
import { ProductCard } from "../components/ProductCard";
import { useAsync } from "../utils/useAsync";

export default function Home() {
  const products = useAsync(() => catalogApi.products({ sort: "newest", page_size: 8 }), []);
  const categories = useAsync(() => catalogApi.categories(), []);

  return (
    <>
      <section className="hero">
        <div className="container">
          <h1>Everything you need, delivered.</h1>
          <p>Electronics, books, fashion, home and outdoor gear — shop securely with fast checkout.</p>
          <Link to="/products" className="btn btn-primary btn-lg">
            Browse the catalog
          </Link>
        </div>
      </section>
      <div className="container">
        <section className="section">
          <h2>Shop by category</h2>
          {categories.loading ? (
            <Loading />
          ) : (
            <>
              <ErrorBox message={categories.error} onRetry={categories.reload} />
              <div className="category-grid">
                {categories.data?.map((c) => (
                  <Link key={c.id} to={`/category/${c.slug}`} className="category-tile">
                    <strong>{c.name}</strong>
                    <span className="muted small">{c.product_count} products</span>
                  </Link>
                ))}
              </div>
            </>
          )}
        </section>
        <section className="section">
          <div className="section-head">
            <h2>New arrivals</h2>
            <Link to="/products">View all →</Link>
          </div>
          {products.loading ? (
            <Loading />
          ) : (
            <>
              <ErrorBox message={products.error} onRetry={products.reload} />
              <div className="product-grid">
                {products.data?.items.map((p) => <ProductCard key={p.id} product={p} />)}
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}
