import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { authHeaders, type OnboardingProfile } from "../lib/api";
import InvoiceImportPanel from "../components/InvoiceImportPanel";

export default function SettingsPage() {
  const { token } = useAuth();
  const [onboardIncome, setOnboardIncome] = useState("");
  const [onboardLocation, setOnboardLocation] = useState("");
  const [onboardGoals, setOnboardGoals] = useState("");
  const [onboardRisk, setOnboardRisk] = useState("moderate");
  const [onboardCurrency, setOnboardCurrency] = useState("INR");
  const [csvText, setCsvText] = useState(
    "occurred_on,amount,category,description,currency\n2026-04-01,-1200,Rent,April rent,INR",
  );
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadProfile = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/users/onboarding/profile", { headers: authHeaders(token) });
    if (!res.ok) return;
    const data = (await res.json()) as OnboardingProfile;
    if (!data.saved) return;
    if (data.monthly_income != null) setOnboardIncome(String(data.monthly_income));
    if (data.location) setOnboardLocation(data.location);
    if (data.goals.length) setOnboardGoals(data.goals.join(", "));
    if (data.risk_tolerance) setOnboardRisk(data.risk_tolerance);
    if (data.currency) setOnboardCurrency(data.currency);
  }, [token]);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  async function saveOnboarding(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    setStatus(null);
    setLoading(true);
    try {
      const goals = onboardGoals
        .split(",")
        .map((g) => g.trim())
        .filter(Boolean);
      const res = await fetch("/api/users/onboarding", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({
          monthly_income: Number(onboardIncome),
          location: onboardLocation,
          goals,
          risk_tolerance: onboardRisk,
          currency: onboardCurrency,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setStatus("Financial profile saved. FinMate will use this in chat context.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setLoading(false);
    }
  }

  async function importCsv() {
    if (!token) return;
    setError(null);
    setStatus(null);
    setLoading(true);
    try {
      const res = await fetch("/api/transactions/import/csv", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({ csv_text: csvText }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as {
        imported_count: number;
        skipped_count: number;
        sample_errors?: string[];
      };
      setStatus(
        `Imported ${data.imported_count} rows, skipped ${data.skipped_count}.` +
          (data.sample_errors?.length ? ` Errors: ${data.sample_errors.slice(0, 2).join("; ")}` : ""),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setLoading(false);
    }
  }

  async function downloadSamplePdf() {
    if (!token) return;
    setError(null);
    try {
      const res = await fetch("/api/invoices/pdf", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({
          line_items: [
            { description: "Consulting", amount: "150.00" },
            { description: "Hosting", amount: "29.99" },
          ],
          currency: "USD",
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "invoice-sample.pdf";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "PDF failed");
    }
  }

  return (
    <div className="settings-page">
      <header className="settings-header">
        <div>
          <Link to="/chat" className="back-link">
            ← Back to chat
          </Link>
          <h1>Settings</h1>
          <p className="muted">Manage your profile, transactions, and tools</p>
        </div>
      </header>

      <div className="settings-grid">
        <section className="settings-card">
          <h2>Financial profile</h2>
          <p className="muted">Used by budget and investment agents for personalized advice.</p>
          <form onSubmit={saveOnboarding} className="settings-form">
            <label htmlFor="income">Monthly income</label>
            <input
              id="income"
              value={onboardIncome}
              onChange={(e) => setOnboardIncome(e.target.value)}
              required
            />
            <label htmlFor="location">Location</label>
            <input id="location" value={onboardLocation} onChange={(e) => setOnboardLocation(e.target.value)} required />
            <label htmlFor="goals">Goals (comma separated)</label>
            <input id="goals" value={onboardGoals} onChange={(e) => setOnboardGoals(e.target.value)} />
            <label htmlFor="risk">Risk tolerance</label>
            <select id="risk" value={onboardRisk} onChange={(e) => setOnboardRisk(e.target.value)}>
              <option value="conservative">Conservative</option>
              <option value="moderate">Moderate</option>
              <option value="aggressive">Aggressive</option>
            </select>
            <label htmlFor="currency">Currency</label>
            <input id="currency" value={onboardCurrency} onChange={(e) => setOnboardCurrency(e.target.value)} />
            <button type="submit" className="btn-primary" disabled={loading}>
              Save profile
            </button>
          </form>
        </section>

        <section className="settings-card">
          <h2>Import transactions</h2>
          <p className="muted">Paste CSV with columns: occurred_on, amount, category, description, currency</p>
          <textarea value={csvText} onChange={(e) => setCsvText(e.target.value)} rows={8} />
          <button type="button" className="btn-secondary" onClick={importCsv} disabled={loading}>
            Import CSV
          </button>
        </section>

        <InvoiceImportPanel onStatus={setStatus} onError={setError} />

        <section className="settings-card">
          <h2>Quick sample PDF</h2>
          <p className="muted">Generate a demo invoice PDF from hard-coded line items.</p>
          <button type="button" className="btn-secondary" onClick={downloadSamplePdf}>
            Download sample PDF
          </button>
        </section>
      </div>

      {status && <p className="status-text">{status}</p>}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
