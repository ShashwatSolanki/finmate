import { useState } from "react";
import { useAuth } from "../lib/auth";
import { authHeaders, downloadBlob, type StructuredInvoice } from "../lib/api";

type Props = { metadata: Record<string, string> };

export default function InvoiceExportActions({ metadata }: Props) {
  const { token } = useAuth();
  const [busy, setBusy] = useState(false);
  let invoice: StructuredInvoice | null = null;
  try {
    invoice = metadata.invoice_payload ? (JSON.parse(metadata.invoice_payload) as StructuredInvoice) : null;
  } catch {
    invoice = null;
  }
  if (!invoice?.line_items?.length || !token) return null;
  const structured = invoice;

  async function downloadPdf() {
    setBusy(true);
    try {
      const response = await fetch("/api/invoices/pdf/structured", {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify(structured),
      });
      if (!response.ok) throw new Error(await response.text());
      downloadBlob(await response.blob(), `invoice-${structured.invoice_number || "draft"}.pdf`);
    } finally {
      setBusy(false);
    }
  }

  function downloadCsv() {
    const quote = (value: string | number | null | undefined) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const rows = [
      ["invoice_number", "invoice_date", "vendor", "bill_to", "currency", "description", "quantity", "unit_price", "amount"],
      ...structured.line_items.map((item) => [structured.invoice_number, structured.invoice_date, structured.vendor_name, structured.bill_to, structured.currency, item.description, item.quantity, item.unit_price, item.amount]),
    ];
    downloadBlob(new Blob([rows.map((row) => row.map(quote).join(",")).join("\n")], { type: "text/csv;charset=utf-8" }), `invoice-${structured.invoice_number || "draft"}.csv`);
  }

  return (
    <div className="invoice-export-actions">
      <span>Export this invoice</span>
      <button type="button" className="btn-primary sm" disabled={busy} onClick={() => void downloadPdf()}>{busy ? "Preparing…" : "Download PDF"}</button>
      <button type="button" className="btn-secondary sm" disabled={busy} onClick={downloadCsv}>Download CSV</button>
    </div>
  );
}
