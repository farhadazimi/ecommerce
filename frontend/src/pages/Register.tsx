import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { ErrorBox } from "../components/common";
import { useAuth } from "../context/AuthContext";

export function fieldErrors(e: unknown): Record<string, string> {
  if (e instanceof ApiError && e.code === "VALIDATION_ERROR" && Array.isArray(e.details)) {
    return Object.fromEntries(
      (e.details as { field: string; message: string }[]).map((d) => [d.field, d.message.replace(/^Value error, /, "")]),
    );
  }
  return {};
}

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/";
  const [form, setForm] = useState({ full_name: "", email: "", password: "", confirm: "" });
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setFields({});
    if (form.password !== form.confirm) {
      setFields({ confirm: "Passwords do not match" });
      return;
    }
    setBusy(true);
    try {
      await register(form.email, form.password, form.full_name);
      navigate(from, { replace: true });
    } catch (err) {
      setFields(fieldErrors(err));
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="container page narrow">
      <form className="card form" onSubmit={submit}>
        <h1>Create your account</h1>
        <label>
          Full name
          <input required minLength={2} maxLength={120} value={form.full_name} onChange={set("full_name")} autoComplete="name" />
          {fields.full_name && <span className="field-error">{fields.full_name}</span>}
        </label>
        <label>
          E-mail
          <input type="email" required value={form.email} onChange={set("email")} autoComplete="email" />
          {fields.email && <span className="field-error">{fields.email}</span>}
        </label>
        <label>
          Password
          <input type="password" required minLength={8} maxLength={72} value={form.password} onChange={set("password")}
            autoComplete="new-password" />
          <span className="muted small">At least 8 characters, including a letter and a digit.</span>
          {fields.password && <span className="field-error">{fields.password}</span>}
        </label>
        <label>
          Confirm password
          <input type="password" required value={form.confirm} onChange={set("confirm")} autoComplete="new-password" />
          {fields.confirm && <span className="field-error">{fields.confirm}</span>}
        </label>
        <ErrorBox message={error} />
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Creating account…" : "Register"}
        </button>
        <p className="muted small">
          Already registered? <Link to="/login">Log in</Link>
        </p>
      </form>
    </div>
  );
}
