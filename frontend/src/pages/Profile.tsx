import { useState, type FormEvent } from "react";
import { errorMessage } from "../api/client";
import { profileApi } from "../api/endpoints";
import { ErrorBox, Notice, ProductImage } from "../components/common";
import { useAuth } from "../context/AuthContext";
import { formatDate } from "../utils/format";

export default function Profile() {
  const { user, setUser } = useAuth();
  const p = user?.profile;
  const [form, setForm] = useState({
    full_name: user?.full_name ?? "", phone: p?.phone ?? "", address_line1: p?.address_line1 ?? "",
    address_line2: p?.address_line2 ?? "", city: p?.city ?? "", postal_code: p?.postal_code ?? "", country: p?.country ?? "",
  });
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!user) return null;

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const save = (e: FormEvent) => {
    e.preventDefault();
    void guard(async () => {
      const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim() === "" && k !== "full_name" ? null : v.trim()]));
      setUser(await profileApi.update(body));
      setMsg("Profile saved.");
    });
  };

  const changePassword = (e: FormEvent) => {
    e.preventDefault();
    void guard(async () => {
      await profileApi.changePassword(pw.current_password, pw.new_password);
      setPw({ current_password: "", new_password: "" });
      setMsg("Password changed.");
    });
  };

  const onAvatar = (file: File | undefined) => {
    if (!file) return;
    void guard(async () => {
      setUser(await profileApi.uploadAvatar(file));
      setMsg("Avatar updated.");
    });
  };

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="container page">
      <h1>My profile</h1>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorBox message={error} />
      <div className="profile-layout">
        <div className="card profile-card">
          <ProductImage src={p?.avatar_url} alt={user.full_name} className="avatar" />
          <strong>{user.full_name}</strong>
          <span className="muted small">{user.email}</span>
          <span className="muted small">Member since {formatDate(user.created_at)}</span>
          <label className="btn btn-sm">
            Upload avatar
            <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden disabled={busy}
              onChange={(e) => onAvatar(e.target.files?.[0])} />
          </label>
        </div>
        <form className="card form" onSubmit={save}>
          <h2>Details</h2>
          <label>Full name<input required minLength={2} maxLength={120} value={form.full_name} onChange={set("full_name")} /></label>
          <label>Phone<input maxLength={32} value={form.phone} onChange={set("phone")} /></label>
          <label>Address<input maxLength={255} value={form.address_line1} onChange={set("address_line1")} /></label>
          <label>Address line 2<input maxLength={255} value={form.address_line2} onChange={set("address_line2")} /></label>
          <div className="row-3">
            <label>City<input maxLength={100} value={form.city} onChange={set("city")} /></label>
            <label>Postal code<input maxLength={20} value={form.postal_code} onChange={set("postal_code")} /></label>
            <label>Country<input maxLength={100} value={form.country} onChange={set("country")} /></label>
          </div>
          <button type="submit" className="btn btn-primary" disabled={busy}>Save profile</button>
        </form>
        <form className="card form" onSubmit={changePassword}>
          <h2>Change password</h2>
          <label>Current password<input type="password" required autoComplete="current-password" value={pw.current_password}
            onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></label>
          <label>New password<input type="password" required minLength={8} maxLength={72} autoComplete="new-password"
            value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></label>
          <button type="submit" className="btn" disabled={busy}>Change password</button>
        </form>
      </div>
    </div>
  );
}
