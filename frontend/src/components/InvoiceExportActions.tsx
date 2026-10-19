import { useState } from "react";
import { useAuth } from "../lib/auth";
import { apiFetch, authHeaders, downloadBlob, type StructuredInvoice } from "../lib/api";
import { sanitizeDownloadBaseName } from "../lib/filename";

type Props = { metadata: Record<string, string> };

export default function InvoiceExportActions({ metadata }: Props) {
  const { token } = useAuth();
  const [busy, setBusy] = useState(false);
  const [customFilename, setCustomFilename] = useState("");
  let invoice: StructuredInvoice | null = null;
  try {
    invoice = metadata.invoice_payload ? (JSON.parse(metadata.invoice_payload) as StructuredInvoice) : null;
  } catch {
    invoice = null;
  }
  if (!invoice?.line_items?.length || !token) return null;
  const structured = invoice;
  const defaultBaseName = `invoice-${structured.invoice_number || "draft"}`;
  const downloadBaseName = () => sanitizeDownloadBaseName(customFilename, defaultBaseName);

  async function downloadPdf() {
    setBusy(true);
    try {
      const response = await apiFetch("/api/invoices/pdf/structured", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify(structured),
      });
      if (!response.ok) throw new Error(await response.text());
      downloadBlob(await response.blob(), `${downloadBaseName()}.pdf`);
    } finally {
      setBusy(false);
    }
  }

  function downloadCsv() {
    const quote = (value: string | number | null | undefined) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const rows = [
      [
        "record_type",
        "invoice_number",
        "invoice_date",
        "due_date",
        "vendor",
        "bill_to",
        "currency",
        "description",
        "quantity",
        "unit_price",
        "amount",
        "subtotal",
        "tax",
        "tax_breakdown",
        "total",
        "notes",
      ],
      ...structured.line_items.map((item) => [
        "line_item",
        structured.invoice_number,
        structured.invoice_date,
        structured.due_date,
        structured.vendor_name,
        structured.bill_to,
        structured.currency,
        item.description,
        item.quantity,
        item.unit_price,
        item.amount,
        "",
        "",
        "",
        "",
        "",
      ]),
      [
        "summary",
        structured.invoice_number,
        structured.invoice_date,
        structured.due_date,
        structured.vendor_name,
        structured.bill_to,
        structured.currency,
        "",
        "",
        "",
        "",
        structured.subtotal,
        structured.tax,
        JSON.stringify(structured.tax_breakdown ?? []),
        structured.total,
        structured.notes,
      ],
    ];
    downloadBlob(new Blob([rows.map((row) => row.map(quote).join(",")).join("\n")], { type: "text/csv;charset=utf-8" }), `${downloadBaseName()}.csv`);
  }

  return (
    <div className="invoice-export-actions">
      <span>Export this invoice</span>
      <label className="invoice-export-filename">
        Download filename
        <input
          type="text"
          value={customFilename}
          onChange={(event) => setCustomFilename(event.target.value)}
          placeholder={defaultBaseName}
          maxLength={120}
          aria-label="Custom invoice download filename"
        />
        <small>Optional; leave blank to use {defaultBaseName}.</small>
      </label>
      <button type="button" className="btn-primary sm" disabled={busy} onClick={() => void downloadPdf()}>{busy ? "Preparing…" : "Download PDF"}</button>
      <button type="button" className="btn-secondary sm" disabled={busy} onClick={downloadCsv}>Download CSV</button>
    </div>
  );
}
