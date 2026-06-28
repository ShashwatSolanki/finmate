import { useState } from "react";
import { useAuth } from "../lib/auth";
import {
  authHeaders,
  authHeadersMultipart,
  type ParseInvoiceResult,
  type StructuredInvoice,
} from "../lib/api";

type Props = {
  onStatus: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

const EMPTY: StructuredInvoice = {
  invoice_number: null,
  invoice_date: null,
  due_date: null,
  vendor_name: null,
  bill_to: null,
  currency: "USD",
  line_items: [],
  subtotal: null,
  tax: null,
  total: null,
  notes: null,
};

export default function InvoiceImportPanel({ onStatus, onError }: Props) {
  const { token } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [parseResult, setParseResult] = useState<ParseInvoiceResult | null>(null);
  const [invoice, setInvoice] = useState<StructuredInvoice>(EMPTY);
  const [loading, setLoading] = useState(false);

  async function parseUpload() {
    if (!token || !file) return;
    onError(null);
    onStatus(null);
    setLoading(true);
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
      setParseResult(data);
      setInvoice(data.invoice);
      onStatus(
        `Parsed ${data.source_type.toUpperCase()} invoice (${data.confidence} confidence) — ${data.invoice.line_items.length} line items.`,
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : "Parse failed");
    } finally {
      setLoading(false);
    }
  }

  async function downloadPdf() {
    if (!token || invoice.line_items.length === 0) return;
    onError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/invoices/pdf/structured", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify(invoice),
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `invoice-${invoice.invoice_number || "export"}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      onStatus("PDF downloaded.");
    } catch (err) {
      onError(err instanceof Error ? err.message : "PDF failed");
    } finally {
      setLoading(false);
    }
  }

  function updateField<K extends keyof StructuredInvoice>(key: K, value: StructuredInvoice[K]) {
    setInvoice((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <section className="settings-card invoice-card">
      <h2>Invoice import (PDF / image)</h2>
      <p className="muted">
        Upload a PDF or photo of an invoice. FinMate extracts structured fields (vendor, dates, line items, totals)
        and can regenerate a clean PDF.
      </p>

      <label htmlFor="invoice-file">File (PDF, PNG, JPEG, WebP)</label>
      <input
        id="invoice-file"
        type="file"
        accept=".pdf,image/png,image/jpeg,image/webp,image/jpg"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />

      <div className="invoice-actions">
        <button type="button" className="btn-primary" onClick={parseUpload} disabled={loading || !file}>
          {loading ? "Processing…" : "Extract structured data"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={downloadPdf}
          disabled={loading || invoice.line_items.length === 0}
        >
          Download PDF
        </button>
      </div>

      {parseResult && parseResult.warnings.length > 0 && (
        <ul className="invoice-warnings">
          {parseResult.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {invoice.line_items.length > 0 && (
        <div className="invoice-structured">
          <h3>Structured data (editable)</h3>
          <div className="invoice-fields">
            <label>
              Invoice #
              <input
                value={invoice.invoice_number ?? ""}
                onChange={(e) => updateField("invoice_number", e.target.value || null)}
              />
            </label>
            <label>
              Date
              <input
                value={invoice.invoice_date ?? ""}
                onChange={(e) => updateField("invoice_date", e.target.value || null)}
              />
            </label>
            <label>
              Vendor
              <input
                value={invoice.vendor_name ?? ""}
                onChange={(e) => updateField("vendor_name", e.target.value || null)}
              />
            </label>
            <label>
              Bill to
              <input
                value={invoice.bill_to ?? ""}
                onChange={(e) => updateField("bill_to", e.target.value || null)}
              />
            </label>
            <label>
              Currency
              <input value={invoice.currency} onChange={(e) => updateField("currency", e.target.value)} />
            </label>
          </div>

          <table className="invoice-table">
            <thead>
              <tr>
                <th>Description</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {invoice.line_items.map((li, idx) => (
                <tr key={`${li.description}-${idx}`}>
                  <td>
                    <input
                      value={li.description}
                      onChange={(e) => {
                        const next = [...invoice.line_items];
                        next[idx] = { ...li, description: e.target.value };
                        updateField("line_items", next);
                      }}
                    />
                  </td>
                  <td>
                    <input
                      value={li.amount}
                      onChange={(e) => {
                        const next = [...invoice.line_items];
                        next[idx] = { ...li, amount: e.target.value };
                        updateField("line_items", next);
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {invoice.total && <p className="invoice-total">Total: {invoice.total} {invoice.currency}</p>}

          <details className="invoice-preview">
            <summary>Extracted text preview</summary>
            <pre>{parseResult?.extracted_text_preview}</pre>
          </details>
        </div>
      )}
    </section>
  );
}
