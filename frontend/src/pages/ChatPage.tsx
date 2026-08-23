import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import ChatComposerMenu from "../components/ChatComposerMenu";
import ChatSidebar from "../components/ChatSidebar";
import MessageMetadata from "../components/MessageMetadata";
import InvoiceExportActions from "../components/InvoiceExportActions";
import { useAuth } from "../lib/auth";
import {
  authHeaders,
  cleanAssistantText,
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

export default function ChatPage() {
  const { token } = useAuth();
  const threadRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agent, setAgent] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const loadConversations = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/conversations", { headers: authHeaders(token) });
    if (!res.ok) return;
    const data = (await res.json()) as Conversation[];
    setConversations(data);
  }, [token]);

  const loadMessages = useCallback(
    async (sessionId: string) => {
      if (!token) return;
      const res = await fetch(`/api/conversations/${sessionId}/messages`, {
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
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
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
    await fetch(`/api/conversations/${id}`, {
      method: "DELETE",
      headers: authHeaders(token),
    });
    if (activeSessionId === id) {
      setActiveSessionId(null);
      setMessages([]);
    }
    await loadConversations();
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

      const res = await fetch("/api/chat/message", {
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

  function onInvoiceImported(invoice: StructuredInvoice, source: string) {
    const linePreview = invoice.line_items.map((item) => `• ${item.description} — ${item.amount} ${invoice.currency}`).join("\n");
    const content = [
      `Imported invoice ${invoice.invoice_number || "draft"} from ${source}.`,
      invoice.vendor_name ? `From: ${invoice.vendor_name}` : null,
      invoice.bill_to ? `Bill to: ${invoice.bill_to}` : null,
      "",
      linePreview,
      "",
      `Subtotal: ${invoice.subtotal ?? "—"} ${invoice.currency}`,
      `GST / tax: ${invoice.tax ?? "0.00"} ${invoice.currency}`,
      `Total: ${invoice.total ?? "—"} ${invoice.currency}`,
    ].filter((line): line is string => line !== null).join("\n");
    setMessages((previous) => [...previous, {
      id: `invoice-${Date.now()}`,
      role: "assistant",
      content,
      agent: "invoice_generator",
      metadata: { source: "invoice_csv_import", invoice_payload: JSON.stringify(invoice), invoice_actions: "pdf,csv" },
      created_at: new Date().toISOString(),
    }]);
    shouldAutoScrollRef.current = true;
  }

  function onTransactionsImported(
    rows: Array<{ amount: string; currency: string; category: string | null; description: string | null; occurred_on: string }>,
    source: string,
    importedCount: number,
  ) {
    const preview = rows.map((row) => `• ${row.occurred_on} — ${row.description || row.category || "Transaction"}: ${row.amount} ${row.currency}`).join("\n");
    const more = importedCount > rows.length ? `\n…plus ${importedCount - rows.length} more transaction${importedCount - rows.length === 1 ? "" : "s"}.` : "";
    setMessages((previous) => [...previous, {
      id: `transactions-${Date.now()}`,
      role: "assistant",
      content: `Imported ${importedCount} transaction${importedCount === 1 ? "" : "s"} from ${source}.\n\n${preview}${more}`,
      agent: "budget_planner",
      metadata: { source: "transaction_csv_import" },
      created_at: new Date().toISOString(),
    }]);
    shouldAutoScrollRef.current = true;
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

          {messages.map((m) => (
            <div key={m.id} className={`bubble-row ${m.role}`}>
              <div className={`bubble ${m.role}`}>
                {m.role === "assistant" && m.agent && (
                  <span className="agent-badge">{m.agent.replace(/_/g, " ")}</span>
                )}
                <p>{m.content}</p>
                {m.role === "assistant" && m.metadata && <InvoiceExportActions metadata={m.metadata} />}
                {m.role === "assistant" && m.metadata && (
                  <MessageMetadata metadata={m.metadata} />
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="bubble-row assistant">
              <div className="bubble assistant typing">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {(error || status) && (
          <div className="chat-feedback">
            {status && <p className="chat-status">{status}</p>}
            {error && <p className="chat-error">{error}</p>}
          </div>
        )}

        <form className="chat-composer" onSubmit={onSend}>
          <ChatComposerMenu
            disabled={loading}
            activeSessionId={activeSessionId}
            messages={messages}
            onImportStatus={setStatus}
            onImportError={setError}
            onInvoiceImported={onInvoiceImported}
            onTransactionsImported={onTransactionsImported}
          />
          <textarea
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
          <button type="submit" className="btn-primary" disabled={loading || !input.trim()}>
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

