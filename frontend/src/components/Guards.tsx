import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { Loading } from "./common";

export function RequireAuth({ children, role }: { children: ReactNode; role?: "ADMIN" }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  if (role && user.role !== role) {
    return (
      <div className="container">
        <div className="alert alert-error">You do not have permission to view this page.</div>
      </div>
    );
  }
  return <>{children}</>;
}
