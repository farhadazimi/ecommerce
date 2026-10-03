import { useState } from "react";
import { errorMessage } from "../../api/client";
import { adminApi } from "../../api/endpoints";
import { ErrorBox, Loading, Pagination } from "../../components/common";
import { useAuth } from "../../context/AuthContext";
import { formatDate } from "../../utils/format";
import { useAsync } from "../../utils/useAsync";

export default function AdminUsers() {
  const { user: me } = useAuth();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("");
  const [page, setPage] = useState(1);
  const list = useAsync(() => adminApi.users({ q: query, role, page, page_size: 20 }), [query, role, page]);
  const [error, setError] = useState<string | null>(null);

  const toggle = async (id: number, active: boolean) => {
    setError(null);
    try {
      const updated = await adminApi.setUserActive(id, active);
      list.setData({ ...list.data!, items: list.data!.items.map((u) => (u.id === id ? updated : u)) });
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <>
      <h1>Users</h1>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setPage(1); setQuery(q.trim()); }}>
        <input type="search" placeholder="Name or e-mail" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={role} onChange={(e) => { setPage(1); setRole(e.target.value); }} aria-label="Role filter">
          <option value="">All roles</option>
          <option value="CUSTOMER">Customers</option>
          <option value="ADMIN">Admins</option>
        </select>
        <button className="btn btn-sm" type="submit">Search</button>
      </form>
      <ErrorBox message={error} />
      {list.loading ? <Loading /> : list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : (
        <div className="card">
          <table className="table">
            <thead><tr><th>Name</th><th>E-mail</th><th>Role</th><th>Registered</th><th>Last login</th><th>Status</th><th /></tr></thead>
            <tbody>
              {list.data?.items.map((u) => (
                <tr key={u.id}>
                  <td>{u.full_name}</td>
                  <td>{u.email}</td>
                  <td>{u.role}</td>
                  <td>{formatDate(u.created_at)}</td>
                  <td>{formatDate(u.last_login_at)}</td>
                  <td>{u.is_active ? <span className="badge badge-paid">Active</span> : <span className="badge badge-cancelled">Disabled</span>}</td>
                  <td>
                    {u.id !== me?.id && (
                      <button type="button" className={u.is_active ? "btn btn-sm btn-danger" : "btn btn-sm"} onClick={() => toggle(u.id, !u.is_active)}>
                        {u.is_active ? "Deactivate" : "Activate"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.data && <Pagination page={list.data.page} pages={list.data.pages} onChange={setPage} />}
        </div>
      )}
    </>
  );
}
