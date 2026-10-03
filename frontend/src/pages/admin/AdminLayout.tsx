import { NavLink, Outlet } from "react-router-dom";

const LINKS = [
  ["/admin", "Dashboard"],
  ["/admin/products", "Products"],
  ["/admin/categories", "Categories"],
  ["/admin/inventory", "Inventory"],
  ["/admin/orders", "Orders"],
  ["/admin/users", "Users"],
] as const;

export default function AdminLayout() {
  return (
    <div className="container page admin">
      <nav className="admin-nav" aria-label="Admin">
        {LINKS.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/admin"}>
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="admin-content">
        <Outlet />
      </div>
    </div>
  );
}
