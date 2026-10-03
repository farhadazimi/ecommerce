import { useState, type FormEvent } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";

export function Header() {
  const { user, logout } = useAuth();
  const { count } = useCart();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    navigate(q.trim() ? `/search?q=${encodeURIComponent(q.trim())}` : "/products");
  };

  const onLogout = async () => {
    setOpen(false);
    await logout();
    navigate("/");
  };

  return (
    <header className="site-header">
      <div className="container header-inner">
        <Link to="/" className="brand">
          🛍️ Plan B Shop
        </Link>
        <form className="search" onSubmit={onSearch} role="search">
          <input
            type="search"
            placeholder="Search products…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search products"
            maxLength={100}
          />
        </form>
        <nav className="nav">
          <NavLink to="/products">Shop</NavLink>
          {user?.role === "ADMIN" && <NavLink to="/admin">Admin</NavLink>}
          <NavLink to="/cart" className="cart-link" aria-label={`Cart with ${count} items`}>
            🛒<span className="cart-badge">{count}</span>
          </NavLink>
          {user ? (
            <div className="user-menu">
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
                {user.full_name.split(" ")[0]} ▾
              </button>
              {open && (
                <div className="dropdown" onMouseLeave={() => setOpen(false)}>
                  <Link to="/profile" onClick={() => setOpen(false)}>
                    Profile
                  </Link>
                  <Link to="/orders" onClick={() => setOpen(false)}>
                    My orders
                  </Link>
                  {user.role === "ADMIN" && (
                    <Link to="/admin" onClick={() => setOpen(false)}>
                      Admin dashboard
                    </Link>
                  )}
                  <button type="button" onClick={onLogout}>
                    Log out
                  </button>
                </div>
              )}
            </div>
          ) : (
            <>
              <NavLink to="/login">Log in</NavLink>
              <Link to="/register" className="btn btn-primary btn-sm">
                Sign up
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
