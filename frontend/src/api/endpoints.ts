import { request, type Query } from "./client";
import type {
  AuthResponse, Cart, CardInput, Category, InventoryRow, Order, Page, Payment, Product, ProductInput,
  ShippingInfo, Statistics, User,
} from "./types";

export const authApi = {
  me: () => request<User>("/api/auth/me", { silent401: true }),
  login: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/login", { method: "POST", body: { email, password }, silent401: true }),
  register: (email: string, password: string, full_name: string) =>
    request<AuthResponse>("/api/auth/register", { method: "POST", body: { email, password, full_name } }),
  logout: () => request<{ message: string }>("/api/auth/logout", { method: "POST", silent401: true }),
};

export const profileApi = {
  get: () => request<User>("/api/profile"),
  update: (data: Partial<Record<string, string | null>>) => request<User>("/api/profile", { method: "PUT", body: data }),
  changePassword: (current_password: string, new_password: string) =>
    request<{ message: string }>("/api/profile/password", { method: "PUT", body: { current_password, new_password } }),
  uploadAvatar: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<User>("/api/profile/avatar", { method: "POST", body: form });
  },
};

export const catalogApi = {
  products: (query: Query) => request<Page<Product>>("/api/products", { query }),
  product: (id: number | string) => request<Product>(`/api/products/${id}`),
  categories: () => request<Category[]>("/api/categories"),
};

export const cartApi = {
  get: () => request<Cart>("/api/cart"),
  add: (product_id: number, quantity: number) => request<Cart>("/api/cart/items", { method: "POST", body: { product_id, quantity } }),
  update: (itemId: number, quantity: number) => request<Cart>(`/api/cart/items/${itemId}`, { method: "PUT", body: { quantity } }),
  remove: (itemId: number) => request<Cart>(`/api/cart/items/${itemId}`, { method: "DELETE" }),
  clear: () => request<Cart>("/api/cart", { method: "DELETE" }),
};

export const orderApi = {
  create: (shipping: ShippingInfo, idempotencyKey: string) =>
    request<Order>("/api/orders", { method: "POST", body: shipping, headers: { "Idempotency-Key": idempotencyKey } }),
  list: (query: Query) => request<Page<Order>>("/api/orders", { query }),
  get: (id: number | string) => request<Order>(`/api/orders/${id}`),
  cancel: (id: number, reason?: string) => request<Order>(`/api/orders/${id}/cancel`, { method: "POST", body: { reason: reason ?? null } }),
  invoice: (id: number) => request<{ order_id: number; invoice_number: string; url: string; expires_in: number }>(`/api/orders/${id}/invoice`),
};

export const paymentApi = {
  create: (order_id: number) => request<Payment>("/api/payment/create", { method: "POST", body: { order_id } }),
  confirm: (payment_id: number, card: CardInput) =>
    request<{ payment: Payment; order: Order }>("/api/payment/confirm", { method: "POST", body: { payment_id, ...card } }),
};

export const adminApi = {
  statistics: () => request<Statistics>("/api/admin/statistics"),
  users: (query: Query) => request<Page<User>>("/api/admin/users", { query }),
  setUserActive: (id: number, is_active: boolean) =>
    request<User>(`/api/admin/users/${id}/status`, { method: "PUT", body: { is_active } }),
  orders: (query: Query) => request<Page<Order>>("/api/admin/orders", { query }),
  order: (id: number | string) => request<Order>(`/api/admin/orders/${id}`),
  setOrderStatus: (id: number, status: string, note?: string) =>
    request<Order>(`/api/admin/orders/${id}/status`, { method: "PUT", body: { status, note: note || null } }),
  inventory: (query: Query) => request<Page<InventoryRow>>("/api/admin/inventory", { query }),
  updateInventory: (productId: number, body: { quantity?: number; adjust?: number; low_stock_threshold?: number }) =>
    request<InventoryRow>(`/api/admin/inventory/${productId}`, { method: "PUT", body }),
  products: (query: Query) => request<Page<Product>>("/api/products", { query: { ...query, include_inactive: true } }),
  createProduct: (body: ProductInput) => request<Product>("/api/products", { method: "POST", body }),
  updateProduct: (id: number, body: Partial<ProductInput>) => request<Product>(`/api/products/${id}`, { method: "PUT", body }),
  deleteProduct: (id: number) => request<void>(`/api/products/${id}`, { method: "DELETE" }),
  uploadImage: (id: number, file: File, isPrimary: boolean) => {
    const form = new FormData();
    form.append("file", file);
    form.append("is_primary", String(isPrimary));
    return request<Product>(`/api/products/${id}/images`, { method: "POST", body: form });
  },
  deleteImage: (productId: number, imageId: number) =>
    request<void>(`/api/products/${productId}/images/${imageId}`, { method: "DELETE" }),
  createCategory: (body: { name: string; description?: string | null }) =>
    request<Category>("/api/categories", { method: "POST", body }),
  updateCategory: (id: number, body: { name?: string; description?: string | null }) =>
    request<Category>(`/api/categories/${id}`, { method: "PUT", body }),
  deleteCategory: (id: number) => request<void>(`/api/categories/${id}`, { method: "DELETE" }),
};
