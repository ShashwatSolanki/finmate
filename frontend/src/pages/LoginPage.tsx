import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

declare global {
  interface Window {
    google?: any;
  }
}

export default function LoginPage() {
  const { setToken } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const googleBtnRef = useRef<HTMLDivElement>(null);
  const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;

  // Handler for official Google Identity Services credential response
  const handleGoogleCredentialResponse = useCallback(
    async (response: { credential?: string }) => {
      if (!response.credential) return;
      setError(null);
      setLoading(true);
      try {
        const res = await fetch("/api/auth/google", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ credential: response.credential }),
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || "Google Sign-in failed");
        }
        const data = (await res.json()) as { access_token: string; refresh_token?: string };
        setToken(data.access_token, data.refresh_token);
        navigate("/chat");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Google Sign-in failed");
      } finally {
        setLoading(false);
      }
    },
    [navigate, setToken]
  );

  // Initialize official Google button if client ID is provided
  useEffect(() => {
    if (!clientId) return;
    function initGsi() {
      if (window.google?.accounts?.id && googleBtnRef.current) {
        window.google.accounts.id.initialize({
          client_id: clientId,
          callback: handleGoogleCredentialResponse,
        });
        window.google.accounts.id.renderButton(googleBtnRef.current, {
          theme: "outline",
          size: "large",
          width: 356,
          text: "continue_with",
          shape: "rectangular",
        });
      }
    }
    initGsi();
    const timer = setInterval(() => {
      if (window.google?.accounts?.id && googleBtnRef.current) {
        initGsi();
        clearInterval(timer);
      }
    }, 250);
    return () => clearInterval(timer);
  }, [clientId, handleGoogleCredentialResponse]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Login failed");
      }
      const data = (await res.json()) as { access_token: string; refresh_token?: string };
      setToken(data.access_token, data.refresh_token);
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  async function onDevGoogleSignIn() {
    setError(null);
    const targetEmail = email.trim().toLowerCase();
    if (!targetEmail) {
      setError("Please enter your Google / Gmail address in the email field above to sign in with Google.");
      return;
    }

    setLoading(true);
    try {
      // Deterministic, isolated Google Sub based on email to ensure consistent session isolation
      const safeId = targetEmail.replace(/[^a-zA-Z0-9]/g, "_");
      const mockCredential = `mock-google-token:${targetEmail}:google-sub-${safeId}:${targetEmail.split("@")[0]}`;
      const res = await fetch("/api/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential: mockCredential }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Google Sign-in failed");
      }
      const data = (await res.json()) as { access_token: string; refresh_token?: string };
      setToken(data.access_token, data.refresh_token);
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Google Sign-in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="brand-mark">FM</span>
          <h1>Welcome back</h1>
          <p className="muted">Sign in to continue with FinMate</p>
        </div>
        <form onSubmit={onSubmit} className="auth-form">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            placeholder="you@example.com"
            required
          />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: "0.75rem" }}>
            <label htmlFor="password" style={{ margin: 0 }}>Password</label>
            <Link to="/forgot-password" style={{ fontSize: "0.85rem" }}>Forgot password?</Link>
          </div>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            placeholder="Your password"
            required
            minLength={8}
          />
          {error && <p className="error-text">{error}</p>}
          <button type="submit" className="btn-primary" disabled={loading} style={{ marginTop: "1rem" }}>
            {loading ? "Signing in…" : "Sign in"}
          </button>

          <div style={{ margin: "14px 0", textAlign: "center" }}>
            <span style={{ fontSize: "12px", color: "var(--color-text-muted, #888)" }}>or continue with</span>
          </div>

          {clientId ? (
            <div style={{ display: "flex", justifyContent: "center", width: "100%" }}>
              <div ref={googleBtnRef}></div>
            </div>
          ) : (
            <button
              type="button"
              onClick={onDevGoogleSignIn}
              className="btn-secondary"
              style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: "8px" }}
              disabled={loading}
            >
              <svg width="18" height="18" viewBox="0 0 24 24">
                <path
                  fill="#4285F4"
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                />
                <path
                  fill="#34A853"
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                />
                <path
                  fill="#FBBC05"
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                />
                <path
                  fill="#EA4335"
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                />
              </svg>
              Sign in with Google
            </button>
          )}
        </form>
        <p className="auth-switch">
          New here? <Link to="/register">Create an account</Link>
        </p>
      </div>
    </div>
  );
}
