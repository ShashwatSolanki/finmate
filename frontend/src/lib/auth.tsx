import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { TOKEN_KEY } from "./api";

export const REFRESH_TOKEN_KEY = "finmate_refresh_token";

type AuthContextValue = {
  token: string | null;
  refreshToken: string | null;
  setToken: (token: string | null, refreshToken?: string | null) => void;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [refreshToken, setRefreshTokenState] = useState<string | null>(() => localStorage.getItem(REFRESH_TOKEN_KEY));

  const setToken = useCallback((value: string | null, rfValue?: string | null) => {
    if (value) {
      localStorage.setItem(TOKEN_KEY, value);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
    setTokenState(value);

    if (rfValue !== undefined) {
      if (rfValue) localStorage.setItem(REFRESH_TOKEN_KEY, rfValue);
      else localStorage.removeItem(REFRESH_TOKEN_KEY);
      setRefreshTokenState(rfValue);
    }
  }, []);

  const logout = useCallback(async () => {
    const currentRf = localStorage.getItem(REFRESH_TOKEN_KEY);
    if (currentRf) {
      try {
        await fetch("/api/auth/logout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: currentRf }),
        });
      } catch {
        // ignore network error on logout
      }
    }
    setToken(null, null);
  }, [setToken]);

  const value = useMemo(
    () => ({ token, refreshToken, setToken, logout }),
    [token, refreshToken, setToken, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
