import { useEffect, useRef, useState } from "react";
import { useAuth } from "../lib/auth";
import {
  authHeaders,
  authHeadersMultipart,
  downloadBlob,
  type ChatMessage,
  type ParseInvoiceResult,
} from "../lib/api";

type Props = {
  disabled?: boolean;
  activeSessionId: string | null;
  messages: ChatMessage[];
  onImportStatus: (msg: string) => void;
  onImportError: (msg: string | null) => void;
  onInvoiceParsed: (summary: string) => void;
};

export default function ChatComposerMenu({
  disabled,
  activeSessionId,
  messages,
  onImportStatus,
  onImportError,
  onInvoiceParsed,
}: Props) {
  const { token } = useAuth();
  const [importOpen, setImportOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const invoiceInputRef = useRef<HTMLInputElement>(null);
  const csvInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!menuRef.current?.contains(e.target as Node)) {
        setImportOpen(false);
        setExportOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  async function handleInvoiceFile(file: File) {
    if (!token) return;
    setBusy(true);
    onImportError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/invoices/parse", {
        method: "POST",
        headers: authHeadersMultipart(token),
        body: form,
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as ParseInvoiceResult;

      const lines = data.invoice.line_items.map(
        (li) => `${li.description} ${li.amount}`,
      );
      const summary = [
        `Uploaded invoice: ${data.filename}`,
        data.invoice.vendor_name ? `Vendor: ${data.invoice.vendor_name}` : null,
        data.invoice.invoice_date ? `Date: ${data.invoice.invoice_date}` : null,
        data.invoice.total ? `Total: ${data.invoice.total} ${data.invoice.currency}` : null,
        lines.length ? `Line items:\n${lines.join("\n")}` : null,
        data.warnings.length ? `Notes: ${data.warnings.join("; ")}` : null,
      ]
        .filter(Boolean)
        .join("\n");

      onInvoiceParsed(summary);
      onImportStatus(
        `Parsed ${data.source_type.toUpperCase()} invoice — ${data.invoice.line_items.length} line items (${data.confidence} confidence).`,
      );
    } catch (err) {
      onImportError(err instanceof Error ? err.message : "Invoice parse failed");
    } finally {
      setBusy(false);
      setImportOpen(false);
    }
  }

  async function handleCsvFile(file: File) {
    if (!token) return;
    setBusy(true);
    onImportError(null);
    try {
      const csvText = await file.text();
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
      onImportStatus(
        `Imported ${data.imported_count} transactions from ${file.name}, skipped ${data.skipped_count}.` +
          (data.sample_errors?.length ? ` ${data.sample_errors.slice(0, 2).join("; ")}` : ""),
      );
    } catch (err) {
      onImportError(err instanceof Error ? err.message : "CSV import failed");
    } finally {
      setBusy(false);
      setImportOpen(false);
    }
  }

  async function exportTransactions() {
    if (!token) return;
    setBusy(true);
    onImportError(null);
    try {
      const res = await fetch("/api/transactions/export/csv", { headers: authHeaders(token) });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      downloadBlob(blob, "finmate-transactions.csv");
      onImportStatus("Transactions exported as CSV.");
    } catch (err) {
      onImportError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(false);
      setExportOpen(false);
    }
  }

  function exportConversation() {
    if (messages.length === 0) {
      onImportError("No messages to export.");
      return;
    }
    const title = activeSessionId ? `conversation-${activeSessionId.slice(0, 8)}` : "conversation";
    const body = messages
      .map((m) => {
        const who = m.role === "user" ? "You" : m.agent?.replace(/_/g, " ") || "FinMate";
        return `[${m.created_at}] ${who}:\n${m.content}`;
      })
      .join("\n\n---\n\n");
    downloadBlob(new Blob([body], { type: "text/plain;charset=utf-8" }), `${title}.txt`);
    onImportStatus("Conversation downloaded.");
    setExportOpen(false);
  }

  const isDisabled = disabled || busy;

  return (
    <div className="composer-toolbar" ref={menuRef}>
      <input
        ref={invoiceInputRef}
        type="file"
        accept=".pdf,image/png,image/jpeg,image/webp,image/jpg,image/tiff,image/bmp"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void handleInvoiceFile(file);
        }}
      />
      <input
        ref={csvInputRef}
        type="file"
        accept=".csv,text/csv"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void handleCsvFile(file);
        }}
      />

      <div className="composer-menu-wrap">
        <button
          type="button"
          className="composer-icon-btn"
          aria-label="Import file"
          title="Import invoice or CSV"
          disabled={isDisabled}
          onClick={() => {
            setExportOpen(false);
            setImportOpen((v) => !v);
          }}
        >
          +
        </button>
        {importOpen && (
          <div className="composer-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              onClick={() => invoiceInputRef.current?.click()}
            >
              Invoice PDF or image
            </button>
            <button type="button" role="menuitem" onClick={() => csvInputRef.current?.click()}>
              Import transactions CSV
            </button>
          </div>
        )}
      </div>

      <div className="composer-menu-wrap">
        <button
          type="button"
          className="composer-icon-btn"
          aria-label="Export or download"
          title="Export data"
          disabled={isDisabled}
          onClick={() => {
            setImportOpen(false);
            setExportOpen((v) => !v);
          }}
        >
          ↓
        </button>
        {exportOpen && (
          <div className="composer-menu" role="menu">
            <button type="button" role="menuitem" onClick={() => void exportTransactions()}>
              Download transactions (CSV)
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={exportConversation}
              disabled={messages.length === 0}
            >
              Download conversation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
