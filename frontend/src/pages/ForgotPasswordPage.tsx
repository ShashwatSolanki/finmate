import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<"request" | "reset" | "complete">("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleRequestCode(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to request password reset code");
      }
      setStep("reset");
      setInfo("If an account exists for this email, a reset code has been sent.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleResetPassword(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters long");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          code: code.trim(),
          new_password: newPassword,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to reset password");
      }
      setStep("complete");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Password reset failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="brand-mark">FM</span>
          {step === "request" && (
            <>
              <h1>Forgot password</h1>
              <p className="muted">Enter your email to receive a password reset code</p>
            </>
          )}
          {step === "reset" && (
            <>
              <h1>Reset password</h1>
              <p className="muted">Enter the code sent to your email and set a new password</p>
            </>
          )}
          {step === "complete" && (
            <>
              <h1>Password updated</h1>
              <p className="muted">Your password has been reset successfully</p>
            </>
          )}
        </div>

        {error && <p className="error-text">{error}</p>}
        {info && <p className="status-text">{info}</p>}

        {step === "request" && (
          <form onSubmit={handleRequestCode} className="auth-form">
            <label htmlFor="email">Email address</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              placeholder="you@example.com"
              required
            />
            <button type="submit" className="btn-primary" disabled={loading} style={{ marginTop: "1.25rem" }}>
              {loading ? "Sending code…" : "Send reset code"}
            </button>
            <p className="auth-switch">
              Remember your password? <Link to="/login">Sign in</Link>
            </p>
          </form>
        )}

        {step === "reset" && (
          <form onSubmit={handleResetPassword} className="auth-form">
            <label htmlFor="emailDisplay">Email address</label>
            <input id="emailDisplay" type="email" value={email} disabled style={{ background: "#f8fafc" }} />

            <label htmlFor="code">Verification / Reset Code</label>
            <input
              id="code"
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="e.g. 123456"
              autoComplete="one-time-code"
              required
            />

            <label htmlFor="newPassword">New Password (min 8 chars)</label>
            <input
              id="newPassword"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              required
              minLength={8}
            />

            <label htmlFor="confirmPassword">Confirm New Password</label>
            <input
              id="confirmPassword"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              required
              minLength={8}
            />

            <button type="submit" className="btn-primary" disabled={loading} style={{ marginTop: "1.25rem" }}>
              {loading ? "Updating password…" : "Reset password"}
            </button>

            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setStep("request");
                setError(null);
                setInfo(null);
              }}
              style={{ marginTop: "0.75rem", width: "100%" }}
            >
              Back / Change email
            </button>
          </form>
        )}

        {step === "complete" && (
          <div style={{ marginTop: "1.5rem", textAlign: "center" }}>
            <p style={{ color: "#0f766e", fontWeight: 600, marginBottom: "1.5rem" }}>
              You can now sign in using your new password.
            </p>
            <button
              type="button"
              className="btn-primary"
              onClick={() => navigate("/login")}
              style={{ width: "100%" }}
            >
              Sign in now
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
