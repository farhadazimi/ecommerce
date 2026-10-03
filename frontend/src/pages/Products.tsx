import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { catalogApi } from "../api/endpoints";
import { Empty, ErrorBox, Loading, Pagination } from "../components/common";
import { ProductCard } from "../components/ProductCard";
import { useAsync } from "../utils/useAsync";

/** Product listing; also used for /category/:slug and /search?q=. */
export default function Products({ mode = "all" }: { mode?: "all" | "category" | "search" }) {
  const { slug } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const q = params.get("q") ?? "";
  const category = mode === "category" ? (slug ?? "") : (params.get("category") ?? "");
  const minPrice = params.get("min_price") ?? "";
  const maxPrice = params.get("max_price") ?? "";
  const inStock = params.get("in_stock") === "true";
  const sort = params.get("sort") ?? "newest";
  const page = Number(params.get("page") ?? "1") || 1;

  const [draft, setDraft] = useState({ q, minPrice, maxPrice });
  useEffect(() => setDraft({ q, minPrice, maxPrice }), [q, minPrice, maxPrice]);

  const categories = useAsync(() => catalogApi.categories(), []);
  const products = useAsync(
    () =>
      catalogApi.products({
        q, category, min_price: minPrice, max_price: maxPrice, in_stock: inStock || undefined, sort, page, page_size: 12,
      }),
    [q, category, minPrice, maxPrice, inStock, sort, page],
  );

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    if (!("page" in changes)) next.delete("page");
    setParams(next);
  };

  const applyFilters = (e: FormEvent) => {
    e.preventDefault();
    update({ q: draft.q.trim(), min_price: draft.minPrice, max_price: draft.maxPrice });
  };

  const currentCategory = categories.data?.find((c) => c.slug === category || String(c.id) === category);
  const title =
    mode === "category" ? (currentCategory?.name ?? "Category") : mode === "search" ? `Search results for “${q}”` : "All products";

  return (
    <div className="container page">
      <h1>{title}</h1>
      {mode === "category" && currentCategory?.description && <p className="muted">{currentCategory.description}</p>}
      <div className="listing">
        <aside className="filters card">
          <form onSubmit={applyFilters}>
            <label>
              Search
              <input value={draft.q} onChange={(e) => setDraft({ ...draft, q: e.target.value })} maxLength={100} />
            </label>
            {mode !== "category" && (
              <label>
                Category
                <select
                  value={category}
                  onChange={(e) => update({ category: e.target.value })}
                  disabled={categories.loading}
                >
                  <option value="">All categories</option>
                  {categories.data?.map((c) => (
                    <option key={c.id} value={c.slug}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="row-2">
              <label>
                Min price
                <input type="number" min={0} step="0.01" value={draft.minPrice}
                  onChange={(e) => setDraft({ ...draft, minPrice: e.target.value })} />
              </label>
              <label>
                Max price
                <input type="number" min={0} step="0.01" value={draft.maxPrice}
                  onChange={(e) => setDraft({ ...draft, maxPrice: e.target.value })} />
              </label>
            </div>
            <label className="checkbox">
              <input type="checkbox" checked={inStock} onChange={(e) => update({ in_stock: e.target.checked ? "true" : null })} />
              In stock only
            </label>
            <label>
              Sort by
              <select value={sort} onChange={(e) => update({ sort: e.target.value })}>
                <option value="newest">Newest</option>
                <option value="price_asc">Price: low to high</option>
                <option value="price_desc">Price: high to low</option>
                <option value="name">Name</option>
              </select>
            </label>
            <button className="btn btn-primary" type="submit">Apply</button>
            <button className="btn btn-ghost" type="button"
              onClick={() => (mode === "category" ? setParams(new URLSearchParams()) : navigate("/products"))}>
              Reset
            </button>
          </form>
        </aside>
        <section>
          {products.loading ? (
            <Loading />
          ) : products.error ? (
            <ErrorBox message={products.error} onRetry={products.reload} />
          ) : products.data && products.data.items.length > 0 ? (
            <>
              <p className="muted small">{products.data.total} product(s)</p>
              <div className="product-grid">
                {products.data.items.map((p) => <ProductCard key={p.id} product={p} />)}
              </div>
              <Pagination page={products.data.page} pages={products.data.pages} onChange={(p) => update({ page: String(p) })} />
            </>
          ) : (
            <Empty>No products match your filters.</Empty>
          )}
        </section>
      </div>
    </div>
  );
}
