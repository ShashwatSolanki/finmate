import { useEffect, useRef, useState } from "react";
import { useAuth } from "../lib/auth";
import {
  authHeaders,
  authHeadersMultipart,
  downloadBlob,
  type ChatMessage,
  type ParseInvoiceResult,
  type StructuredInvoice,
} from "../lib/api";

type Props = {
  disabled?: boolean;
  activeSessionId: string | null;
  messages: ChatMessage[];
  onImportStatus: (msg: string) => void;
  onImportError: (msg: string | null) => void;
  onInvoiceImported: (invoice: StructuredInvoice, source: string) => void;
  onTransactionsImported: (rows: ImportedTransactionPreview[], source: string, importedCount: number) => void;
};

type ImportedTransactionPreview = {
  amount: string;
  currency: string;
  category: string | null;
  description: string | null;
  occurred_on: string;
};

export default function ChatComposerMenu({
  disabled,
  activeSessionId,
  messages,
  onImportStatus,
  onImportError,
  onInvoiceImported,
  onTransactionsImported,
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
      // Keep the structured result available to Invoice Studio without turning a
      // file import into a second, slow chat/LLM request.
      sessionStorage.setItem("finmate_pending_invoice", JSON.stringify(data.invoice));
      onInvoiceImported(data.invoice, file.name);

      onImportStatus(
        `Parsed ${data.source_type.toUpperCase()} invoice — ${data.invoice.line_items.length} line items (${data.confidence} confidence). Open Settings → Invoice Studio to review, edit, or download it.`,
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
      const header = csvText.split(/\r?\n/, 1)[0].toLowerCase();
      if (header.includes("invoice_no") && header.includes("item") && header.includes("amount")) {
        const form = new FormData();
        form.append("file", file);
        const invoiceResponse = await fetch("/api/invoices/parse/csv", {
          method: "POST",
          headers: authHeadersMultipart(token),
          body: form,
        });
        if (!invoiceResponse.ok) throw new Error(await invoiceResponse.text());
        const invoiceData = (await invoiceResponse.json()) as ParseInvoiceResult;
        sessionStorage.setItem("finmate_pending_invoice", JSON.stringify(invoiceData.invoice));
        onInvoiceImported(invoiceData.invoice, file.name);
        onImportStatus(`Imported invoice CSV — ${invoiceData.invoice.line_items.length} line items. Ready to export or edit in Invoice Studio.`);
        return;
      }
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
        imported_preview?: ImportedTransactionPreview[];
      };
      if (data.imported_count) {
        onTransactionsImported(data.imported_preview ?? [], file.name, data.imported_count);
      }
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
              Import CSV (transactions or invoice)
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
