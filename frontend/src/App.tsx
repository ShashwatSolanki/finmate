import { useCallback, useState } from "react";

type ChatResponse = {
  agent: string;
  reply: string;
  planned_steps: string[];
  metadata?: Record<string, string>;
};
type ChatTurn = {
  id: number;
  role: "user" | "assistant";
  text: string;
  agent?: string;
};

const AGENTS = [
  { value: "", label: "Auto (hybrid routing)" },
  { value: "budget_planner", label: "Budget Planner" },
  { value: "invoice_generator", label: "Invoice Generator" },
  { value: "investment_analyser", label: "Investment Analyser" },
] as const;

const TOKEN_KEY = "finmate_token";

function authHeaders(token: string | null): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [agent, setAgent] = useState("");
  const [message, setMessage] = useState("Help me understand my spending vs last month.");
  const [onboardIncome, setOnboardIncome] = useState("50000");
  const [onboardLocation, setOnboardLocation] = useState("India");
  const [onboardGoals, setOnboardGoals] = useState("Build emergency fund,Long-term investing");
  const [onboardRisk, setOnboardRisk] = useState("moderate");
  const [onboardCurrency, setOnboardCurrency] = useState("INR");
  const [csvText, setCsvText] = useState("occurred_on,amount,category,description,currency\n2026-04-01,-1200,Rent,April rent,INR");
  const [reply, setReply] = useState<ChatResponse | null>(null);
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const cleanAssistantText = useCallback((raw: string) => {
    const lines = raw.replace(/\r/g, "").split("\n").map((l) => l.trimEnd());
    const filtered = lines.filter((line) => {
      const t = line.trim();
      if (!t) return true;
      if (/^\[AGENT:\s*[A-Z_]+\]$/i.test(t)) return false;
      if (t.startsWith("{") && t.endsWith("}")) {
        try {
          JSON.parse(t);
          return false;
        } catch {
          return true;
        }
      }
      return true;
    });
    return filtered.join("\n").trim();
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setReply(null);
    setHistory([]);
  }, []);

  const register = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          display_name: displayName || undefined,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { access_token: string };
      localStorage.setItem(TOKEN_KEY, data.access_token);
      setToken(data.access_token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Register failed");
    } finally {
      setLoading(false);
    }
  }, [displayName, email, password]);

  const login = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { access_token: string };
      localStorage.setItem(TOKEN_KEY, data.access_token);
      setToken(data.access_token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }, [email, password]);

  const send = useCallback(async () => {
    setError(null);
    setReply(null);
    if (!token) {
      setError("Register or log in first.");
      return;
    }
    setLoading(true);
    setHistory((prev) => [...prev, { id: Date.now(), role: "user", text: message }]);
    try {
      const payload: Record<string, unknown> = { message };
      if (agent) payload.agent = agent;
      const res = await fetch("/api/chat/message", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || res.statusText);
      }
      const data = (await res.json()) as ChatResponse;
      setReply(data);
      setHistory((prev) => [
        ...prev,
        {
          id: Date.now() + 1,
          role: "assistant",
          text: cleanAssistantText(data.reply) || data.reply,
          agent: data.agent,
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }, [agent, cleanAssistantText, message, token]);

  const downloadSamplePdf = useCallback(async () => {
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
    } catch (e) {
      setError(e instanceof Error ? e.message : "PDF failed");
    }
  }, [token]);

  const saveOnboarding = useCallback(async () => {
    if (!token) return;
    setError(null);
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
      const data = (await res.json()) as { profile_summary: string };
      setHistory((prev) => [...prev, { id: Date.now(), role: "assistant", text: `Onboarding saved.\n${data.profile_summary}` }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Onboarding save failed");
    } finally {
      setLoading(false);
    }
  }, [onboardCurrency, onboardGoals, onboardIncome, onboardLocation, onboardRisk, token]);

  const importCsvTransactions = useCallback(async () => {
    if (!token) return;
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/transactions/import/csv", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({ csv_text: csvText }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { imported_count: number; skipped_count: number; sample_errors?: string[] };
      const lines = [
        `CSV import done: imported=${data.imported_count}, skipped=${data.skipped_count}`,
        ...(data.sample_errors || []).slice(0, 5),
      ];
      setHistory((prev) => [...prev, { id: Date.now(), role: "assistant", text: lines.join("\n") }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "CSV import failed");
    } finally {
      setLoading(false);
    }
  }, [csvText, token]);

  return (
    <div className="app">
      <h1>FinMate</h1>
      <p className="muted">
        JWT auth, RAG memory (Chroma), hybrid intent, Yahoo data via yfinance. Log in to use chat.
      </p>

      {!token ? (
        <div className="panel">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          <label htmlFor="pw" style={{ marginTop: "0.75rem" }}>
            Password (min 8 chars)
          </label>
          <input
            id="pw"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
          <label htmlFor="dn" style={{ marginTop: "0.75rem" }}>
            Display name (optional)
          </label>
          <input id="dn" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          <button type="button" style={{ marginRight: 8 }} onClick={register} disabled={loading}>
            Register
          </button>
          <button type="button" onClick={login} disabled={loading}>
            Log in
          </button>
        </div>
      ) : (
        <div className="panel">
          <span>Logged in.</span>{" "}
          <button type="button" onClick={logout}>
            Log out
          </button>
          <button type="button" style={{ marginLeft: 8 }} onClick={downloadSamplePdf}>
            Download sample PDF invoice
          </button>
        </div>
      )}

      <div className="panel">
        <label htmlFor="agent">Agent</label>
        <select
          id="agent"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
          style={{ width: "100%", padding: "0.5rem 0.65rem", borderRadius: 8, border: "1px solid #cbd5e1" }}
        >
          {AGENTS.map((a) => (
            <option key={a.value || "auto"} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      {token && (
        <div className="panel">
          <strong>Quick onboarding</strong>
          <label htmlFor="oi" style={{ marginTop: "0.75rem" }}>Monthly income</label>
          <input id="oi" value={onboardIncome} onChange={(e) => setOnboardIncome(e.target.value)} />
          <label htmlFor="ol" style={{ marginTop: "0.75rem" }}>Location</label>
          <input id="ol" value={onboardLocation} onChange={(e) => setOnboardLocation(e.target.value)} />
          <label htmlFor="og" style={{ marginTop: "0.75rem" }}>Goals (comma separated)</label>
          <input id="og" value={onboardGoals} onChange={(e) => setOnboardGoals(e.target.value)} />
          <label htmlFor="or" style={{ marginTop: "0.75rem" }}>Risk tolerance</label>
          <input id="or" value={onboardRisk} onChange={(e) => setOnboardRisk(e.target.value)} />
          <label htmlFor="oc" style={{ marginTop: "0.75rem" }}>Currency</label>
          <input id="oc" value={onboardCurrency} onChange={(e) => setOnboardCurrency(e.target.value)} />
          <button type="button" style={{ marginTop: "0.75rem" }} onClick={saveOnboarding} disabled={loading}>
            Save onboarding profile
          </button>
        </div>
      )}

      {token && (
        <div className="panel">
          <strong>Import transactions CSV</strong>
          <textarea value={csvText} onChange={(e) => setCsvText(e.target.value)} style={{ marginTop: "0.75rem" }} />
          <button type="button" style={{ marginTop: "0.5rem" }} onClick={importCsvTransactions} disabled={loading}>
            Import CSV
          </button>
        </div>
      )}

      <div className="panel">
        <label htmlFor="msg">Message</label>
        <textarea id="msg" value={message} onChange={(e) => setMessage(e.target.value)} />
        <button type="button" onClick={send} disabled={loading || !token}>
          {loading ? "Sending…" : "Send"}
        </button>
        {loading && <p className="muted" style={{ marginTop: "0.5rem" }}>FinMate is thinking...</p>}
      </div>

      {history.length > 0 && (
        <div className="panel">
          <strong>Conversation</strong>
          <div style={{ marginTop: "0.75rem", display: "grid", gap: "0.75rem" }}>
            {history.map((turn) => (
              <div key={turn.id} style={{ padding: "0.65rem", border: "1px solid #e2e8f0", borderRadius: 8 }}>
                <strong>{turn.role === "user" ? "You" : `FinMate${turn.agent ? ` (${turn.agent})` : ""}`}</strong>
                <pre style={{ marginTop: "0.5rem" }}>{turn.text}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="panel">
          <strong>Error</strong>
          <pre>{error}</pre>
        </div>
      )}

      {reply && (
        <div className="panel">
          <strong>Agent</strong>
          <pre>{reply.agent}</pre>
          <strong style={{ display: "block", marginTop: "0.75rem" }}>Reply</strong>
          <pre>{cleanAssistantText(reply.reply) || reply.reply}</pre>
          <strong style={{ display: "block", marginTop: "0.75rem" }}>Planned steps</strong>
          <pre>{reply.planned_steps.join(" → ")}</pre>
          {reply.metadata && Object.keys(reply.metadata).length > 0 && (
            <>
              <strong style={{ display: "block", marginTop: "0.75rem" }}>Metadata</strong>
              <pre>{JSON.stringify(reply.metadata, null, 2)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
