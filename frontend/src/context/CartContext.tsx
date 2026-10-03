import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { cartApi } from "../api/endpoints";
import type { Cart } from "../api/types";
import { useAuth } from "./AuthContext";

interface CartState {
  cart: Cart | null;
  count: number;
  refresh: () => Promise<void>;
  setCart: (cart: Cart | null) => void;
}

const CartContext = createContext<CartState | undefined>(undefined);

export function CartProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [cart, setCart] = useState<Cart | null>(null);

  const refresh = useCallback(async () => {
    if (!user) {
      setCart(null);
      return;
    }
    try {
      setCart(await cartApi.get());
    } catch {
      setCart(null);
    }
  }, [user]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo(() => ({ cart, count: cart?.item_count ?? 0, refresh, setCart }), [cart, refresh]);
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartState {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used inside CartProvider");
  return ctx;
}
