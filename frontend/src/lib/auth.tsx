import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { TOKEN_KEY } from "./api";

type AuthContextValue = {
  token: string | null;
  setToken: (token: string | null) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));

  const setToken = useCallback((value: string | null) => {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
    setTokenState(value);
  }, []);

  const logout = useCallback(() => setToken(null), [setToken]);

  const value = useMemo(() => ({ token, setToken, logout }), [token, setToken, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
