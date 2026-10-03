import type { ReactNode } from "react";
import { statusLabel } from "../utils/format";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string | null | undefined; onRetry?: () => void }) {
  if (!message) return null;
  return (
    <div className="alert alert-error" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="btn btn-sm btn-ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "success" | "warning"; children: ReactNode }) {
  return <div className={`alert alert-${kind}`}>{children}</div>;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status.toLowerCase()}`}>{statusLabel(status)}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ProductImage({ src, alt, className }: { src: string | null | undefined; alt: string; className?: string }) {
  if (!src) {
    return (
      <div className={`img-placeholder ${className ?? ""}`} aria-label={alt} role="img">
        <span>{alt.slice(0, 1).toUpperCase()}</span>
      </div>
    );
  }
  return <img src={src} alt={alt} className={className} loading="lazy" />;
}

export function Pagination({ page, pages, onChange }: { page: number; pages: number; onChange: (page: number) => void }) {
  if (pages <= 1) return null;
  return (
    <nav className="pagination" aria-label="Pagination">
      <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        ‹ Prev
      </button>
      <span>
        Page {page} of {pages}
      </span>
      <button type="button" className="btn btn-sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>
        Next ›
      </button>
    </nav>
  );
}
