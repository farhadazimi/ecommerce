import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { errorMessage } from "../api/client";
import { ErrorBox, Notice } from "../components/common";
import { useAuth } from "../context/AuthContext";

export default function Login() {
  const { login, sessionExpired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await login(email, password);
      navigate(from === "/" && user.role === "ADMIN" ? "/admin" : from, { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container page narrow">
      <form className="card form" onSubmit={submit}>
        <h1>Log in</h1>
        {sessionExpired && <Notice kind="warning">Your session has expired. Please log in again.</Notice>}
        <label>
          E-mail
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        <ErrorBox message={error} />
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Logging in…" : "Log in"}
        </button>
        <p className="muted small">
          No account? <Link to="/register" state={{ from }}>Create one</Link>
        </p>
      </form>
    </div>
  );
}
