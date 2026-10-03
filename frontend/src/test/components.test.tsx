import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Product } from "../api/types";
import { ErrorBox, ProductImage, StatusBadge } from "../components/common";
import { ProductCard } from "../components/ProductCard";

const product: Product = {
  id: 7, sku: "EL-1", name: "Wireless Headphones", slug: "wireless-headphones", description: "d", price: 199.99, currency: "USD",
  is_active: true, category: { id: 1, name: "Electronics", slug: "electronics" },
  stock: { available: 3, in_stock: true, low_stock: true }, image_url: null, images: [],
  created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00",
};

describe("components", () => {
  it("renders a product card with price, stock and link", () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><ProductCard product={product} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Wireless Headphones" })).toBeInTheDocument();
    expect(screen.getByText("$199.99")).toBeInTheDocument();
    expect(screen.getByText("Only 3 left")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/products/7");
    expect(screen.getByRole("img", { name: "Wireless Headphones" })).toHaveClass("img-placeholder");
  });

  it("shows out-of-stock state", () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><ProductCard product={{ ...product, stock: { available: 0, in_stock: false, low_stock: false } }} /></MemoryRouter>);
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
  });

  it("renders images when a URL exists", () => {
    render(<ProductImage src="https://obs.example.com/x.webp" alt="X" />);
    expect(screen.getByRole("img", { name: "X" })).toHaveAttribute("src", "https://obs.example.com/x.webp");
  });

  it("renders text content safely (no HTML injection)", () => {
    render(<ErrorBox message={'<img src=x onerror="alert(1)">'} />);
    expect(screen.getByRole("alert")).toHaveTextContent('<img src=x onerror="alert(1)">');
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders status badges", () => {
    render(<StatusBadge status="PENDING_PAYMENT" />);
    expect(screen.getByText("Pending Payment")).toHaveClass("badge-pending_payment");
  });
});
