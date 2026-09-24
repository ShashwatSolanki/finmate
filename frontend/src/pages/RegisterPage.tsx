import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

type RegisterResponse = {
  access_token: string;
  refresh_token?: string;
  is_verified?: boolean;
  requires_verification?: boolean;
  verification_code_preview?: string | null;
};

export default function RegisterPage() {
  const { setToken } = useAuth();
  const navigate = useNavigate();

  const [step, setStep] = useState<"register" | "verify">("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [codePreview, setCodePreview] = useState<string | null>(null);
  const [tempTokens, setTempTokens] = useState<{ access_token: string; refresh_token?: string } | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onRegisterSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          password,
          display_name: displayName || undefined,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Registration failed");
      }
      const data = (await res.json()) as RegisterResponse;
      setTempTokens({ access_token: data.access_token, refresh_token: data.refresh_token });

      if (data.requires_verification || !data.is_verified) {
        if (data.verification_code_preview) {
          setCodePreview(data.verification_code_preview);
        }
        setStep("verify");
        setInfo(`We have sent a verification code to ${email.trim().toLowerCase()}`);
      } else {
        setToken(data.access_token, data.refresh_token);
        navigate("/chat");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  async function onVerifySubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/verify-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          code: verificationCode.trim(),
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Verification failed");
      }
      const data = (await res.json()) as RegisterResponse;
      setToken(data.access_token, data.refresh_token);
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid or expired code");
    } finally {
      setLoading(false);
    }
  }

  async function onResendCode() {
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/resend-verification", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Failed to resend code");
      }
      setInfo("A new verification code has been sent to your email.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend code");
    } finally {
      setLoading(false);
    }
  }

  function handleSkip() {
    if (tempTokens) {
      setToken(tempTokens.access_token, tempTokens.refresh_token);
      navigate("/chat");
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="brand-mark">FM</span>
          {step === "register" ? (
            <>
              <h1>Create account</h1>
              <p className="muted">Start your personal finance assistant</p>
            </>
          ) : (
            <>
              <h1>Verify your email</h1>
              <p className="muted">Enter the 6-digit code sent to your inbox</p>
            </>
          )}
        </div>

        {error && <p className="error-text">{error}</p>}
        {info && <p className="status-text">{info}</p>}

        {step === "register" ? (
          <form onSubmit={onRegisterSubmit} className="auth-form">
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
            <label htmlFor="password">Password (min 8 chars)</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              placeholder="Min 8 chars with uppercase, digit, symbol"
              required
              minLength={8}
            />
            <label htmlFor="displayName">Display name (optional)</label>
            <input
              id="displayName"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              autoComplete="name"
              placeholder="How should FinMate call you?"
            />
            <button type="submit" className="btn-primary" disabled={loading} style={{ marginTop: "1.25rem" }}>
              {loading ? "Creating account…" : "Create account"}
            </button>
            <p className="auth-switch">
              Already have an account? <Link to="/login">Sign in</Link>
            </p>
          </form>
        ) : (
          <form onSubmit={onVerifySubmit} className="auth-form">
            {codePreview && (
              <div
                style={{
                  background: "#f0fdf4",
                  border: "1px solid #bbf7d0",
                  borderRadius: "8px",
                  padding: "0.6rem 0.8rem",
                  fontSize: "0.85rem",
                  color: "#166534",
                  marginBottom: "0.75rem",
                }}
              >
                <strong>Demo Mode Code:</strong> {codePreview}
              </div>
            )}
            <label htmlFor="verificationCode">Verification Code</label>
            <input
              id="verificationCode"
              type="text"
              value={verificationCode}
              onChange={(e) => setVerificationCode(e.target.value)}
              placeholder="6-digit code"
              autoComplete="one-time-code"
              required
              autoFocus
            />

            <button type="submit" className="btn-primary" disabled={loading} style={{ marginTop: "1.25rem" }}>
              {loading ? "Verifying…" : "Verify Email"}
            </button>

            <div style={{ display: "flex", gap: "8px", marginTop: "0.75rem" }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={onResendCode}
                disabled={loading}
                style={{ flex: 1 }}
              >
                Resend code
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={handleSkip}
                style={{ flex: 1 }}
              >
                Skip for now
              </button>
            </div>

            <p className="auth-switch" style={{ marginTop: "1.25rem" }}>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => {
                  setStep("register");
                  setError(null);
                  setInfo(null);
                }}
                style={{ textAlign: "center", cursor: "pointer", color: "var(--teal)" }}
              >
                ← Back to registration
              </button>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
