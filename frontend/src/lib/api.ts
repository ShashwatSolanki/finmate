export const TOKEN_KEY = "finmate_token";

const configuredApiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
export const API_BASE_URL = configuredApiBase
  ? (configuredApiBase.startsWith("http://") || configuredApiBase.startsWith("https://")
      ? configuredApiBase
      : "https://" + configuredApiBase).replace(/\/+$/, "")
  : "";

export function apiUrl(path: string): string {
  return API_BASE_URL + (path.startsWith("/") ? path : "/" + path);
}

export const REFRESH_TOKEN_KEY = "finmate_refresh_token";
export const AUTH_REFRESHED_EVENT = "finmate:auth-refreshed";
export const AUTH_EXPIRED_EVENT = "finmate:auth-expired";

type TokenPair = {
  access_token: string;
  refresh_token: string;
};

let refreshPromise: Promise<string | null> | null = null;

function readStoredAuthValue(key: string): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStoredAuthValue(key: string, value: string): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(key, value);
  } catch {
    // Storage may be blocked; the current request can still use the returned token.
  }
}

function clearStoredAuth(): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_TOKEN_KEY);
    }
  } catch {
    // Ignore storage restrictions; notify the auth provider regardless.
  }
}

function dispatchAuthEvent(name: string): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(name));
}

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = readStoredAuthValue(REFRESH_TOKEN_KEY);
  if (!refreshToken) {
    clearStoredAuth();
    dispatchAuthEvent(AUTH_EXPIRED_EVENT);
    return null;
  }

  let response: Response;
  try {
    response = await fetch(apiUrl("/api/auth/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    // A transient network failure must not sign the user out.
    return null;
  }

  if (!response.ok) {
    if ([400, 401, 403, 422].includes(response.status)) {
      clearStoredAuth();
      dispatchAuthEvent(AUTH_EXPIRED_EVENT);
    }
    return null;
  }

  let tokens: Partial<TokenPair>;
  try {
    tokens = (await response.json()) as Partial<TokenPair>;
  } catch {
    clearStoredAuth();
    dispatchAuthEvent(AUTH_EXPIRED_EVENT);
    return null;
  }

  if (typeof tokens.access_token !== "string" || !tokens.access_token ||
      typeof tokens.refresh_token !== "string" || !tokens.refresh_token) {
    clearStoredAuth();
    dispatchAuthEvent(AUTH_EXPIRED_EVENT);
    return null;
  }

  writeStoredAuthValue(TOKEN_KEY, tokens.access_token);
  writeStoredAuthValue(REFRESH_TOKEN_KEY, tokens.refresh_token);
  dispatchAuthEvent(AUTH_REFRESHED_EVENT);
  return tokens.access_token;
}

function refreshAccessTokenSingleFlight(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = refreshAccessToken().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

/** Fetch an API endpoint and rotate the stored token pair once after an authenticated 401. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = apiUrl(path);
  const initialHeaders = new Headers(init.headers);
  const hasAuthorization = initialHeaders.has("Authorization");
  const storedAccessToken = readStoredAuthValue(TOKEN_KEY);
  if (hasAuthorization && storedAccessToken) {
    initialHeaders.set("Authorization", "Bearer " + storedAccessToken);
  }

  const response = await fetch(url, { ...init, headers: initialHeaders });
  if (response.status !== 401 || !hasAuthorization) return response;

  const refreshedAccessToken = await refreshAccessTokenSingleFlight();
  if (!refreshedAccessToken) return response;

  const retryHeaders = new Headers(init.headers);
  retryHeaders.set("Authorization", "Bearer " + refreshedAccessToken);
  return fetch(url, { ...init, headers: retryHeaders });
}

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
