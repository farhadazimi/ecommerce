export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface Profile {
  phone: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  postal_code: string | null;
  country: string | null;
  avatar_url: string | null;
}

export type Role = "ADMIN" | "CUSTOMER";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  profile: Profile | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

export interface CategoryRef {
  id: number;
  name: string;
  slug: string;
}

export interface Category extends CategoryRef {
  description: string | null;
  product_count: number;
}

export interface ProductImage {
  id: number;
  url: string;
  is_primary: boolean;
}

export interface Product {
  id: number;
  sku: string;
  name: string;
  slug: string;
  description: string | null;
  price: number;
  currency: string;
  is_active: boolean;
  category: CategoryRef | null;
  stock: { available: number; in_stock: boolean; low_stock: boolean };
  image_url: string | null;
  images: ProductImage[];
  created_at: string;
  updated_at: string;
}

export interface CartItem {
  id: number;
  product: { id: number; name: string; slug: string; sku: string; price: number; image_url: string | null; is_active: boolean };
  quantity: number;
  unit_price: number;
  line_total: number;
  available: number;
  in_stock: boolean;
}

export interface Cart {
  id: number | null;
  items: CartItem[];
  item_count: number;
  subtotal: number;
  shipping_fee: number;
  total: number;
  currency: string;
  warnings: string[];
}

export type OrderStatus = "PENDING_PAYMENT" | "PAID" | "PROCESSING" | "SHIPPED" | "DELIVERED" | "CANCELLED";

export interface OrderItem {
  id: number;
  product_id: number | null;
  sku: string;
  product_name: string;
  unit_price: number;
  quantity: number;
  line_total: number;
}

export interface Payment {
  id: number;
  payment_ref: string;
  order_id: number;
  provider: string;
  amount: number;
  currency: string;
  status: "PENDING" | "SUCCEEDED" | "FAILED" | "REFUNDED";
  card_brand: string | null;
  card_last4: string | null;
  failure_code: string | null;
  failure_reason: string | null;
  created_at: string;
  confirmed_at: string | null;
  test_cards?: Record<string, string>;
}

export interface Order {
  id: number;
  order_number: string;
  status: OrderStatus;
  currency: string;
  subtotal: number;
  shipping_fee: number;
  total: number;
  item_count: number;
  created_at: string;
  updated_at: string;
  paid_at: string | null;
  cancelled_at: string | null;
  customer: { id: number; email: string; full_name: string };
  invoice: { number: string | null; available: boolean };
  items: OrderItem[];
  shipping?: {
    name: string;
    phone: string | null;
    address_line1: string;
    address_line2: string | null;
    city: string;
    postal_code: string;
    country: string;
  };
  notes?: string | null;
  payments?: Payment[];
  history?: { from_status: string | null; to_status: string; note: string | null; changed_by: number | null; created_at: string }[];
}

export interface ShippingInfo {
  shipping_name: string;
  shipping_phone?: string | null;
  shipping_address_line1: string;
  shipping_address_line2?: string | null;
  shipping_city: string;
  shipping_postal_code: string;
  shipping_country: string;
  notes?: string | null;
}

export interface CardInput {
  card_number: string;
  card_holder: string;
  expiry_month: number;
  expiry_year: number;
  cvv: string;
}

export interface InventoryRow {
  product_id: number;
  sku: string;
  name: string;
  is_active: boolean;
  quantity: number;
  reserved: number;
  available: number;
  low_stock_threshold: number;
  low_stock: boolean;
  updated_at: string;
}

export interface Statistics {
  products: number;
  active_products: number;
  categories: number;
  users: number;
  customers: number;
  orders: number;
  orders_today: number;
  orders_by_status: Record<string, number>;
  revenue: number;
  currency: string;
  low_stock_products: number;
  out_of_stock_products: number;
  active_users: number;
  recent_orders: { id: number; order_number: string; status: OrderStatus; total: number; customer: string; created_at: string }[];
}

export interface ProductInput {
  sku: string;
  name: string;
  description: string | null;
  price: string;
  category_id: number | null;
  is_active: boolean;
  stock_quantity: number;
  low_stock_threshold: number;
}
