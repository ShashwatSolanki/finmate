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
  const [holdings, setHoldings] = useState<Array<{
    id: string;
    symbol: string;
    quantity: string;
    average_cost: string;
    currency: string;
    last_price?: string | null;
    market_value?: string | null;
    unrealized_profit?: string | null;
    unrealized_profit_pct?: string | null;
  }>>([]);
  const [holdingSymbol, setHoldingSymbol] = useState("");
  const [holdingQuantity, setHoldingQuantity] = useState("");
  const [holdingCost, setHoldingCost] = useState("");
  const [holdingCurrency, setHoldingCurrency] = useState("INR");

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

  async function loadPortfolio() {
    if (!token) return;
    const res = await fetch("/api/portfolio/summary", { headers: authHeaders(token) });
    if (!res.ok) return;
    setHoldings(await res.json());
  }

  useEffect(() => {
    void loadPortfolio();
  }, [token]);

  async function refreshPortfolio() {
    await loadPortfolio();
    setStatus("Portfolio prices refreshed.");
  }

  async function addHolding(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    setStatus(null);
    setLoading(true);
    try {
      const res = await fetch("/api/portfolio/holdings", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({
          symbol: holdingSymbol.trim().toUpperCase(),
          quantity: Number(holdingQuantity),
          average_cost: Number(holdingCost),
          currency: holdingCurrency.trim().toUpperCase(),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadPortfolio();
      setHoldingSymbol("");
      setHoldingQuantity("");
      setHoldingCost("");
      setStatus("Portfolio holding saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save holding");
    } finally {
      setLoading(false);
    }
  }

  async function deleteHolding(symbol: string) {
    if (!token) return;
    setError(null);
    const res = await fetch(`/api/portfolio/holdings/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
      headers: authHeaders(token),
    });
    if (!res.ok) {
      setError(await res.text());
      return;
    }
    await loadPortfolio();
    setStatus(`${symbol} removed from portfolio.`);
  }

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

        <section className="settings-card">
          <h2>Investment portfolio</h2>
          <p className="muted">
            Store your holdings here. FinMate uses quantity and average cost to calculate unrealized P/L from live prices.
          </p>
          <div className="portfolio-toolbar">
            <p className="muted">Add a holding or refresh live prices.</p>
            <button type="button" className="btn-secondary" onClick={() => void refreshPortfolio()} disabled={loading}>
              Refresh prices
            </button>
          </div>
          <form onSubmit={addHolding} className="settings-form">
            <label htmlFor="holding-symbol">Ticker</label>
            <input
              id="holding-symbol"
              value={holdingSymbol}
              onChange={(e) => setHoldingSymbol(e.target.value)}
              placeholder="RELIANCE.NS"
              required
            />
            <label htmlFor="holding-quantity">Quantity</label>
            <input
              id="holding-quantity"
              type="number"
              min="0.000001"
              step="any"
              value={holdingQuantity}
              onChange={(e) => setHoldingQuantity(e.target.value)}
              placeholder="10"
              required
            />
            <label htmlFor="holding-cost">Average cost per unit</label>
            <input
              id="holding-cost"
              type="number"
              min="0.01"
              step="0.01"
              value={holdingCost}
              onChange={(e) => setHoldingCost(e.target.value)}
              placeholder="1450"
              required
            />
            <label htmlFor="holding-currency">Currency</label>
            <input
              id="holding-currency"
              value={holdingCurrency}
              onChange={(e) => setHoldingCurrency(e.target.value)}
              placeholder="INR"
              required
            />
            <button type="submit" className="btn-primary" disabled={loading}>
              Add / update holding
            </button>
          </form>

          {holdings.length > 0 && (() => {
            const currencies = [...new Set(holdings.map((h) => h.currency.toUpperCase()))];
            const sameCurrency = currencies.length === 1;
            const currency = currencies[0] ?? "";
            const invested = holdings.reduce((sum, h) => sum + Number(h.average_cost) * Number(h.quantity), 0);
            const current = holdings.reduce((sum, h) => sum + (h.market_value != null ? Number(h.market_value) : 0), 0);
            const profit = holdings.reduce((sum, h) => sum + (h.unrealized_profit != null ? Number(h.unrealized_profit) : 0), 0);
            const valued = holdings.filter((h) => h.market_value != null).length;
            return (
              <>
                <div className="portfolio-summary">
                  <div><span>Invested</span><strong>{sameCurrency ? invested.toFixed(2) + " " + currency : "Mixed currencies"}</strong></div>
                  <div><span>Current value</span><strong>{sameCurrency && valued ? current.toFixed(2) + " " + currency : sameCurrency ? "—" : "Mixed currencies"}</strong></div>
                  <div><span>Unrealized P/L</span><strong>{sameCurrency && valued ? (profit >= 0 ? "+" : "") + profit.toFixed(2) + " " + currency : sameCurrency ? "—" : "Mixed currencies"}</strong></div>
                  <div><span>Holdings valued</span><strong>{valued}/{holdings.length}</strong></div>
                </div>
                <div className="portfolio-list">
                  {holdings.map((holding) => (
                    <div className="portfolio-row" key={holding.id}>
                      <div>
                        <strong>{holding.symbol}</strong>
                        <span>{holding.quantity} × {holding.average_cost} {holding.currency}</span>
                      </div>
                      <div className="portfolio-values">
                        <span>Price: {holding.last_price ?? "—"}</span>
                        <span>Value: {holding.market_value ?? "—"}</span>
                        <span>
                          P/L: {holding.unrealized_profit ?? "—"}
                          {holding.unrealized_profit_pct != null ? " (" + Number(holding.unrealized_profit_pct).toFixed(2) + "%)" : ""}
                        </span>
                      </div>
                      <button type="button" className="btn-ghost portfolio-remove" onClick={() => void deleteHolding(holding.symbol)}>
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              </>
            );
          })()}
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
