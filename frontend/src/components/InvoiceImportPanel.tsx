import { useEffect, useState } from "react";
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

  useEffect(() => {
    const saved = sessionStorage.getItem("finmate_pending_invoice");
    if (!saved) return;
    try {
      const parsed = JSON.parse(saved) as StructuredInvoice;
      if (Array.isArray(parsed.line_items) && parsed.line_items.length) {
        setInvoice(parsed);
        onStatus("Loaded the invoice you parsed in chat. Review it and download when ready.");
      }
    } catch {
      // Ignore stale browser storage; a fresh upload remains available.
    }
  }, [onStatus]);

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
        body: JSON.stringify({ ...invoice, subtotal: subtotal.toFixed(2), total: total.toFixed(2) }),
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

  function addLineItem() {
    updateField("line_items", [...invoice.line_items, { description: "", quantity: null, unit_price: null, amount: "0.00" }]);
  }

  function removeLineItem(index: number) {
    updateField("line_items", invoice.line_items.filter((_, idx) => idx !== index));
  }

  const subtotal = invoice.line_items.reduce((sum, item) => sum + (Number(item.amount) || 0), 0);
  const tax = Number(invoice.tax) || 0;
  const total = subtotal + tax;

  function updateLine(index: number, changes: Partial<StructuredInvoice["line_items"][number]>) {
    const next = [...invoice.line_items];
    const item = { ...next[index], ...changes };
    if (item.quantity != null && item.unit_price != null && Number(item.quantity) > 0 && Number(item.unit_price) >= 0) {
      item.amount = (Number(item.quantity) * Number(item.unit_price)).toFixed(2);
    }
    next[index] = item;
    updateField("line_items", next);
  }

  return (
    <section className="settings-card invoice-card">
      <h2>Invoice studio</h2>
      <p className="muted">
        Create a polished invoice from scratch, or upload a PDF/photo to extract and edit its details before downloading.
      </p>

      <div className="invoice-structured invoice-create-form">
        <h3>Create an invoice</h3>
        <div className="invoice-fields">
          <label>Invoice #<input value={invoice.invoice_number ?? ""} placeholder="INV-1001" onChange={(e) => updateField("invoice_number", e.target.value || null)} /></label>
          <label>Date<input type="date" value={invoice.invoice_date ?? ""} onChange={(e) => updateField("invoice_date", e.target.value || null)} /></label>
          <label>Due date<input type="date" value={invoice.due_date ?? ""} onChange={(e) => updateField("due_date", e.target.value || null)} /></label>
          <label>Your business<input value={invoice.vendor_name ?? ""} placeholder="Acme Studio" onChange={(e) => updateField("vendor_name", e.target.value || null)} /></label>
          <label>Bill to<input value={invoice.bill_to ?? ""} placeholder="Client name" onChange={(e) => updateField("bill_to", e.target.value || null)} /></label>
          <label>Currency<input value={invoice.currency} onChange={(e) => updateField("currency", e.target.value.toUpperCase())} /></label>
        </div>
        <div className="invoice-table-wrap">
          <table className="invoice-editor-table">
            <thead><tr><th>Item / service</th><th>Qty</th><th>Rate</th><th>Amount</th><th aria-label="Actions" /></tr></thead>
            <tbody>{invoice.line_items.map((li, idx) => (
              <tr key={idx}>
                <td><input aria-label={`Item ${idx + 1} description`} value={li.description} placeholder="Service or item" onChange={(e) => updateLine(idx, { description: e.target.value })} /></td>
                <td><input aria-label={`Item ${idx + 1} quantity`} inputMode="decimal" value={li.quantity ?? ""} placeholder="1" onChange={(e) => updateLine(idx, { quantity: e.target.value ? Number(e.target.value) : null })} /></td>
                <td><input aria-label={`Item ${idx + 1} rate`} inputMode="decimal" value={li.unit_price ?? ""} placeholder="0.00" onChange={(e) => updateLine(idx, { unit_price: e.target.value || null })} /></td>
                <td><input aria-label={`Item ${idx + 1} amount`} inputMode="decimal" value={li.amount} placeholder="0.00" onChange={(e) => updateLine(idx, { amount: e.target.value })} /></td>
                <td><button type="button" className="btn-icon" aria-label="Remove line item" onClick={() => removeLineItem(idx)}>×</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <div className="invoice-totals-editor">
          <label>GST / tax amount<input inputMode="decimal" value={invoice.tax ?? ""} placeholder="0.00" onChange={(e) => updateField("tax", e.target.value || null)} /></label>
          <dl><div><dt>Subtotal</dt><dd>{subtotal.toFixed(2)} {invoice.currency}</dd></div><div><dt>Tax</dt><dd>{tax.toFixed(2)} {invoice.currency}</dd></div><div className="grand-total"><dt>Total</dt><dd>{total.toFixed(2)} {invoice.currency}</dd></div></dl>
        </div>
        <div className="invoice-actions">
          <button type="button" className="btn-secondary" onClick={addLineItem}>+ Add line item</button>
          <button type="button" className="btn-primary" onClick={downloadPdf} disabled={loading || invoice.line_items.length === 0 || invoice.line_items.some((li) => !li.description.trim() || Number(li.amount) <= 0)}>
            {loading ? "Generating…" : "Download invoice PDF"}
          </button>
        </div>
      </div>

      <label htmlFor="invoice-file">Or parse an existing file (PDF, PNG, JPEG, WebP)</label>
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
      </div>

      {parseResult && parseResult.warnings.length > 0 && (
        <ul className="invoice-warnings">
          {parseResult.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {parseResult && invoice.line_items.length > 0 && (
        <details className="invoice-structured parsed-invoice-details">
          <summary>Review parsed details</summary>
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
        </details>
      )}
    </section>
  );
}
