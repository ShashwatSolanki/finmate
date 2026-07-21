export const TOKEN_KEY = "finmate_token";

export type ChatResponse = {
  agent: string;
  reply: string;
  planned_steps: string[];
  metadata?: Record<string, string>;
  session_id?: string;
};

export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  agent: string | null;
  metadata?: Record<string, string> | null;
  created_at: string;
};

export type OnboardingProfile = {
  saved: boolean;
  monthly_income: number | null;
  location: string | null;
  goals: string[];
  risk_tolerance: string | null;
  currency: string | null;
  profile_summary: string;
};

export type ParsedLineItem = {
  description: string;
  quantity: number | null;
  unit_price: string | null;
  amount: string;
};

export type StructuredInvoice = {
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  vendor_name: string | null;
  bill_to: string | null;
  currency: string;
  line_items: ParsedLineItem[];
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  notes: string | null;
};

export type ParseInvoiceResult = {
  source_type: string;
  filename: string;
  confidence: string;
  extracted_text_preview: string;
  invoice: StructuredInvoice;
  warnings: string[];
};

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function authHeadersMultipart(token: string | null): HeadersInit {
  const h: Record<string, string> = {};
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export function authHeaders(token: string | null): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export function cleanAssistantText(raw: string): string {
  const lines = raw.replace(/\r/g, "").split("\n").map((l) => l.trimEnd());
  const filtered = lines.filter((line) => {
    const t = line.trim();
    if (!t) return false;
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
}
