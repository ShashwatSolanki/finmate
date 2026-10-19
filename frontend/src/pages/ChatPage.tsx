import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import ChatComposerMenu from "../components/ChatComposerMenu";
import ChatSidebar from "../components/ChatSidebar";
import MessageMetadata from "../components/MessageMetadata";
import InvoiceExportActions from "../components/InvoiceExportActions";
import { useAuth } from "../lib/auth";
import {
  apiFetch,
  authHeaders,
  cleanAssistantText,
  parseInvoiceWarnings,
  type ChatMessage,
  type ChatResponse,
  type Conversation,
  type StructuredInvoice,
} from "../lib/api";

const AGENTS = [
  { value: "", label: "Auto" },
  { value: "budget_planner", label: "Budget" },
  { value: "invoice_generator", label: "Invoice" },
  { value: "investment_analyser", label: "Investment" },
] as const;

function readImportedInvoice(metadata?: Record<string, string> | null): StructuredInvoice | null {
  if (metadata?.source !== "invoice_import" || !metadata.invoice_payload) return null;
  try {
    const parsed = JSON.parse(metadata.invoice_payload) as Partial<StructuredInvoice>;
    if (typeof parsed.currency !== "string" || !Array.isArray(parsed.line_items)) return null;
    return parsed as StructuredInvoice;
  } catch {
    return null;
  }
}

type ImportedTransactionPreview = {
  amount: string;
  currency: string;
  category: string | null;
  description: string | null;
  occurred_on: string;
};

type TransactionCsvAttachment = {
  filename: string;
  importedCount: number;
  skippedCount: number;
  preview: ImportedTransactionPreview[];
  sampleErrors: string[];
};

function readTransactionCsvAttachment(metadata?: Record<string, string> | null): TransactionCsvAttachment | null {
  if (metadata?.source !== "transaction_csv_import") return null;

  let preview: ImportedTransactionPreview[] = [];
  let sampleErrors: string[] = [];
  try {
    const parsed: unknown = JSON.parse(metadata.preview || "[]");
    if (Array.isArray(parsed)) {
      preview = parsed
        .filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
        .slice(0, 8)
        .map((row) => ({
          amount: String(row.amount ?? ""),
          currency: String(row.currency ?? ""),
          category: row.category == null ? null : String(row.category),
          description: row.description == null ? null : String(row.description),
          occurred_on: String(row.occurred_on ?? ""),
        }));
    }
  } catch {
    preview = [];
  }
  try {
    const parsed: unknown = JSON.parse(metadata.sample_errors || "[]");
    if (Array.isArray(parsed)) {
      sampleErrors = parsed.filter((value): value is string => typeof value === "string").slice(0, 10);
    }
  } catch {
    sampleErrors = [];
  }

  const importedCount = Number(metadata.imported_count || 0);
  const skippedCount = Number(metadata.skipped_count || 0);
  return {
    filename: metadata.filename || "transactions.csv",
    importedCount: Number.isFinite(importedCount) ? importedCount : 0,
    skippedCount: Number.isFinite(skippedCount) ? skippedCount : 0,
    preview,
    sampleErrors,
  };
}

export default function ChatPage() {
  const { token } = useAuth();
  const threadRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agent, setAgent] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [expandedCsvImports, setExpandedCsvImports] = useState<Record<string, boolean>>({});

  const loadConversations = useCallback(async () => {
    if (!token) return;
    const res = await apiFetch("/api/conversations", { headers: authHeaders(token) });
    if (!res.ok) return;
    const data = (await res.json()) as Conversation[];
    setConversations(data);
  }, [token]);

  const loadMessages = useCallback(
    async (sessionId: string) => {
      if (!token) return;
      const res = await apiFetch(`/api/conversations/${sessionId}/messages`, {
        headers: authHeaders(token),
      });
      if (!res.ok) return;
      const data = (await res.json()) as ChatMessage[];
      setMessages(data);
      shouldAutoScrollRef.current = true;
    },
    [token],
  );

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    if (activeSessionId) void loadMessages(activeSessionId);
    else setMessages([]);
  }, [activeSessionId, loadMessages]);

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return;
    const el = threadRef.current;
    if (!el) return;
    // Scroll only the message pane. scrollIntoView() can bubble scrolling to
    // the page/root and make the fixed chat header appear to disappear.
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  function handleThreadScroll() {
    const el = threadRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    shouldAutoScrollRef.current = distanceFromBottom < 96;
  }

  async function startNewChat() {
    if (!token) return;
    setActiveSessionId(null);
    setMessages([]);
    setInput("");
    setError(null);
    setStatus(null);
    shouldAutoScrollRef.current = true;
  }

  async function selectConversation(id: string) {
    setActiveSessionId(id);
    setError(null);
    setStatus(null);
    shouldAutoScrollRef.current = true;
  }

  async function deleteConversation(id: string) {
    if (!token) return;
    await apiFetch(`/api/conversations/${id}`, {
      method: "DELETE",
      headers: authHeaders(token),
    });
    if (activeSessionId === id) {
      setActiveSessionId(null);
      setMessages([]);
    }
    await loadConversations();
  }

  async function renameConversation(id: string, title: string): Promise<boolean> {
    if (!token) return false;
    const cleanedTitle = title.trim();
    if (!cleanedTitle) return false;
    setError(null);
    try {
      const response = await apiFetch(`/api/conversations/${id}`, {
        method: "PATCH",
        headers: authHeaders(token),
        body: JSON.stringify({ title: cleanedTitle }),
      });
      if (!response.ok) throw new Error(await response.text());
      await loadConversations();
      setStatus("Chat renamed.");
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rename chat.");
      return false;
    }
  }

  async function sendMessage(userText: string, agentOverride?: string) {
    if (!token || !userText.trim() || loading) return;

    const trimmed = userText.trim();
    setError(null);
    setLoading(true);
    shouldAutoScrollRef.current = true;

    const optimisticUser: ChatMessage = {
      id: `tmp-${Date.now()}`,
      role: "user",
      content: trimmed,
      agent: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser]);

    try {
      const payload: Record<string, unknown> = { message: trimmed };
      const chosenAgent = agentOverride ?? agent;
      if (chosenAgent) payload.agent = chosenAgent;
      if (activeSessionId) payload.session_id = activeSessionId;

      const res = await apiFetch("/api/chat/message", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await res.text());

      const data = (await res.json()) as ChatResponse;
      const assistantText = cleanAssistantText(data.reply) || data.reply;

      if (data.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id);
      }

      const assistantMsg: ChatMessage = {
        id: `tmp-${Date.now() + 1}`,
        role: "assistant",
        content: assistantText,
        agent: data.agent,
        metadata: data.metadata ?? null,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);

      if (data.session_id) {
        await loadMessages(data.session_id);
      }
      await loadConversations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Send failed");
      setMessages((prev) => prev.filter((m) => m.id !== optimisticUser.id));
      throw err;
    } finally {
      setLoading(false);
    }
  }

  async function onSend(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;
    const userText = input.trim();
    setInput("");
    try {
      await sendMessage(userText);
    } catch {
      setInput(userText);
    }
  }

  async function onInvoiceImported(
    invoice: StructuredInvoice,
    source: string,
    sourceType: string,
    confidence: string,
    warnings: string[],
  ) {
    if (!token) throw new Error("Please sign in again before saving this invoice.");

    let sessionId = activeSessionId;
    let createdSessionId: string | null = null;
    if (!sessionId) {
      const createRes = await apiFetch("/api/conversations", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({ title: `Invoice: ${source}`.slice(0, 200) }),
      });
      if (!createRes.ok) throw new Error(await createRes.text());
      const created = (await createRes.json()) as Conversation;
      sessionId = created.id;
      createdSessionId = created.id;
    }

    let savedMessage: ChatMessage;
    try {
      const res = await apiFetch(`/api/conversations/${sessionId}/invoice-imports`, {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({
          filename: source,
          source_type: sourceType,
          confidence,
          invoice,
          warnings,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      savedMessage = (await res.json()) as ChatMessage;
    } catch (err) {
      // Do not leave an empty sidebar conversation behind if saving a first
      // attachment fails before any user turn exists.
      if (createdSessionId) {
        await apiFetch(`/api/conversations/${createdSessionId}`, {
          method: "DELETE",
          headers: authHeaders(token),
        }).catch(() => undefined);
      }
      throw err;
    }

    setMessages((previous) => [...previous, savedMessage]);
    if (sessionId !== activeSessionId) setActiveSessionId(sessionId);
    shouldAutoScrollRef.current = true;
    // The attachment is already saved; a failed sidebar refresh must not undo
    // or falsely report failure for the persisted conversation event.
    void loadConversations().catch(() => undefined);
  }

  async function onTransactionsImported(
    rows: ImportedTransactionPreview[],
    source: string,
    importedCount: number,
    skippedCount: number,
    sampleErrors: string[],
  ) {
    if (!token) throw new Error("Please sign in again before saving the CSV import.");
    let sessionId = activeSessionId;
    let createdSessionId: string | null = null;

    if (!sessionId) {
      const createRes = await apiFetch("/api/conversations", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({ title: `CSV import: ${source}`.slice(0, 200) }),
      });
      if (!createRes.ok) throw new Error(await createRes.text());
      const created = (await createRes.json()) as Conversation;
      sessionId = created.id;
      createdSessionId = created.id;
    }

    let savedMessage: ChatMessage;
    try {
      const res = await apiFetch(`/api/conversations/${sessionId}/csv-imports`, {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({
          filename: source,
          imported_count: importedCount,
          skipped_count: skippedCount,
          preview: rows.slice(0, 8).map((row) => ({
            amount: String(row.amount),
            currency: row.currency,
            category: row.category,
            description: row.description,
            occurred_on: row.occurred_on,
          })),
          sample_errors: sampleErrors.slice(0, 10),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      savedMessage = (await res.json()) as ChatMessage;
    } catch (err) {
      if (createdSessionId) {
        await apiFetch(`/api/conversations/${createdSessionId}`, {
          method: "DELETE",
          headers: authHeaders(token),
        }).catch(() => undefined);
      }
      throw err;
    }

    setMessages((previous) => [...previous, savedMessage]);
    if (sessionId !== activeSessionId) setActiveSessionId(sessionId);
    shouldAutoScrollRef.current = true;
    void loadConversations().catch(() => undefined);
  }

  return (
    <div className={`app-shell ${sidebarOpen ? "sidebar-open" : ""}`}>
      <ChatSidebar
        conversations={conversations}
        activeId={activeSessionId}
        onSelect={(id) => {
          void selectConversation(id);
          setSidebarOpen(false);
        }}
        onNewChat={() => {
          void startNewChat();
          setSidebarOpen(false);
        }}
        onDelete={deleteConversation}
        onRename={renameConversation}
      />

      <main className="chat-main">
        <header className="chat-header">
          <div className="chat-header-left">
            <button
              type="button"
              className="sidebar-toggle"
              aria-label="Open conversations"
              onClick={() => setSidebarOpen(true)}
            >
              ☰
            </button>
            <div>
              <h2>{activeSessionId ? "Conversation" : "New chat"}</h2>
              <p className="muted">Ask about budget, investments, or invoices</p>
            </div>
          </div>
          <div className="chat-header-actions">
            <select
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              className="agent-select"
              aria-label="Agent"
            >
              {AGENTS.map((a) => (
                <option key={a.value || "auto"} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
            <Link to="/settings" className="btn-secondary sm">
              Settings
            </Link>
          </div>
        </header>

        <div className="chat-thread" ref={threadRef} onScroll={handleThreadScroll}>
          {messages.length === 0 && !loading && (
            <div className="chat-empty">
              <h3>How can I help today?</h3>
              <p>
                Try: &ldquo;How is Microsoft stock doing?&rdquo; or use <strong>+</strong> to upload an
                invoice or import a CSV.
              </p>
            </div>
          )}

          {messages.map((m) => {
            const importedInvoice = m.role === "user" ? readImportedInvoice(m.metadata) : null;
            const invoiceWarnings = importedInvoice ? parseInvoiceWarnings(m.metadata?.warnings) : [];
            const importedCsv = m.role === "user" ? readTransactionCsvAttachment(m.metadata) : null;
            return (
              <div key={m.id} className={`bubble-row ${m.role}`}>
                <div className={`bubble ${m.role}`}>
                  {m.role === "assistant" && m.agent && (
                    <span className="agent-badge">{m.agent.replace(/_/g, " ")}</span>
                  )}
                  {importedInvoice && (
                    <div className="invoice-attachment-card">
                      <div className="invoice-attachment-header">
                        <span className="invoice-attachment-icon" aria-hidden="true">▤</span>
                        <div className="invoice-attachment-file">
                          <strong>{m.metadata?.filename || "Invoice file"}</strong>
                          <small>
                            {(m.metadata?.source_type || "invoice").toUpperCase()} invoice ·{" "}
                            {m.metadata?.confidence || "unknown"} confidence
                          </small>
                        </div>
                      </div>
                      <div className="invoice-attachment-summary">
                        <span>
                          {importedInvoice.line_items.length} line item
                          {importedInvoice.line_items.length === 1 ? "" : "s"}
                        </span>
                        {importedInvoice.vendor_name && <span>{importedInvoice.vendor_name}</span>}
                        {importedInvoice.total !== null && (
                          <strong>Total: {importedInvoice.total} {importedInvoice.currency}</strong>
                        )}
                      </div>
                      {invoiceWarnings.length > 0 && (
                        <div className="invoice-attachment-warnings" role="note">
                          <strong>Review extraction</strong>
                          <ul>
                            {invoiceWarnings.map((warning, index) => (
                              <li key={`${warning}-${index}`}>{warning}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <details className="invoice-attachment-details">
                        <summary>View extracted details</summary>
                        {(importedInvoice.invoice_number || importedInvoice.invoice_date || importedInvoice.bill_to) && (
                          <div className="invoice-attachment-fields">
                            {importedInvoice.invoice_number && <span>Invoice #{importedInvoice.invoice_number}</span>}
                            {importedInvoice.invoice_date && <span>Date: {importedInvoice.invoice_date}</span>}
                            {importedInvoice.bill_to && <span>Bill to: {importedInvoice.bill_to}</span>}
                          </div>
                        )}
                        <ul>
                          {importedInvoice.line_items.map((item, index) => (
                            <li key={`${item.description}-${index}`}>
                              <span>{item.description}</span>
                              <strong>{item.amount} {importedInvoice.currency}</strong>
                            </li>
                          ))}
                        </ul>
                        <div className="invoice-attachment-totals">
                          {importedInvoice.subtotal !== null && (
                            <span>Subtotal: {importedInvoice.subtotal} {importedInvoice.currency}</span>
                          )}
                          {importedInvoice.tax !== null && (
                            <span>Tax: {importedInvoice.tax} {importedInvoice.currency}</span>
                          )}
                        </div>
                      </details>
                    </div>
                  )}
                  {importedCsv && (
                    <div className="csv-attachment-card">
                      <div className="invoice-attachment-header">
                        <span className="invoice-attachment-icon" aria-hidden="true">▤</span>
                        <div className="invoice-attachment-file">
                          <strong>{importedCsv.filename}</strong>
                          <small>TRANSACTION CSV · saved to transaction history</small>
                        </div>
                      </div>
                      <div className="csv-attachment-summary">
                        <strong>{importedCsv.importedCount} imported</strong>
                        {importedCsv.skippedCount > 0 && <span>{importedCsv.skippedCount} skipped</span>}
                      </div>
                      {importedCsv.preview.length > 0 ? (
                        <div className="csv-attachment-details">
                          <button
                            type="button"
                            className="csv-preview-toggle"
                            aria-expanded={Boolean(expandedCsvImports[m.id])}
                            aria-controls={`csv-preview-${m.id}`}
                            onClick={() =>
                              setExpandedCsvImports((previous) => ({
                                ...previous,
                                [m.id]: !previous[m.id],
                              }))
                            }
                          >
                            <span aria-hidden="true">{expandedCsvImports[m.id] ? "▾" : "▸"}</span>
                            {expandedCsvImports[m.id]
                              ? "Hide imported transactions"
                              : `Preview imported transactions (${importedCsv.preview.length})`}
                          </button>
                          {expandedCsvImports[m.id] && (
                            <ul id={`csv-preview-${m.id}`} className="csv-attachment-preview-list">
                              {importedCsv.preview.map((row, index) => (
                                <li key={`${row.occurred_on}-${row.description || row.category || "transaction"}-${index}`}>
                                  <span className="csv-attachment-row-label">
                                    <strong>{row.description || row.category || "Transaction"}</strong>
                                    <small>
                                      {row.occurred_on}{row.category && row.description ? ` · ${row.category}` : ""}
                                    </small>
                                  </span>
                                  <strong>{row.amount} {row.currency}</strong>
                                </li>
                              ))}
                              {importedCsv.importedCount > importedCsv.preview.length && (
                                <li className="csv-attachment-more">
                                  <span>Showing the first {importedCsv.preview.length} of {importedCsv.importedCount} imported rows.</span>
                                </li>
                              )}
                            </ul>
                          )}
                        </div>
                      ) : (
                        <p className="csv-attachment-no-preview">The import was saved, but this record has no stored transaction preview.</p>
                      )}
                      {importedCsv.sampleErrors.length > 0 && (
                        <div className="csv-attachment-errors" role="note">
                          <strong>Rows skipped</strong>
                          <ul>
                            {importedCsv.sampleErrors.slice(0, 3).map((item, index) => (
                              <li key={`${item}-${index}`}>{item}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <p className="csv-attachment-caption">{m.content}</p>
                    </div>
                  )}
                  {!importedCsv && <p>{m.content}</p>}
                  {m.role === "assistant" && m.metadata && <InvoiceExportActions metadata={m.metadata} />}
                  {m.role === "assistant" && m.metadata && (
                    <MessageMetadata metadata={m.metadata} />
                  )}
                </div>
              </div>
            );
          })}

          {loading && (
            <div className="bubble-row assistant">
              <div className="bubble assistant typing">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
        </div>

        {(error || status) && (
          <div className="chat-feedback">
            {status && <p className="chat-status">{status}</p>}
            {error && <p className="chat-error">{error}</p>}
          </div>
        )}

        <form className="chat-composer" onSubmit={onSend}>
          <ChatComposerMenu
            disabled={loading || importBusy}
            activeSessionId={activeSessionId}
            messages={messages}
            onImportStatus={setStatus}
            onImportError={setError}
            onBusyChange={setImportBusy}
            onInvoiceImported={onInvoiceImported}
            onTransactionsImported={onTransactionsImported}
          />
          <textarea
            disabled={loading || importBusy}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Message FinMate…"
            rows={1}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void onSend(e);
              }
            }}
          />
          <button type="submit" className="btn-primary" disabled={loading || importBusy || !input.trim()}>
            Send
          </button>
        </form>
      </main>
      {sidebarOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Close conversations"
          onClick={() => setSidebarOpen(false)}
        />
      )}
    </div>
  );
}

