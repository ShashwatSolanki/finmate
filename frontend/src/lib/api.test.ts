import { afterEach, describe, expect, it, vi } from "vitest";
import { API_BASE_URL, apiFetch, apiUrl, authHeaders, authHeadersMultipart, cleanAssistantText, REFRESH_TOKEN_KEY, TOKEN_KEY } from "./api";


const storage = new Map<string, string>();
const localStorageMock = {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => { storage.set(key, String(value)); },
  removeItem: (key: string) => { storage.delete(key); },
  clear: () => storage.clear(),
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  storage.clear();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
};

describe("API helpers", () => {
  it("builds API URLs for local and separately hosted deployments", () => {
    expect(apiUrl("/api/health")).toBe(API_BASE_URL + "/api/health");
    expect(apiUrl("api/health")).toBe(API_BASE_URL + "/api/health");
  });

  it("refreshes an expired access token, rotates the refresh token, and retries once", async () => {
    vi.stubGlobal("localStorage", localStorageMock);
    storage.set(TOKEN_KEY, "old-access");
    storage.set(REFRESH_TOKEN_KEY, "old-refresh");
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      if (requests.length === 1) return new Response(null, { status: 401 });
      if (requests.length === 2) return jsonResponse({ access_token: "new-access", refresh_token: "new-refresh" });
      return jsonResponse({ ok: true });
    }));

    const response = await apiFetch("/api/transactions", { headers: authHeaders("stale-react-state") });

    expect(response.status).toBe(200);
    expect(requests).toHaveLength(3);
    expect(new Headers(requests[0].init?.headers).get("Authorization")).toBe("Bearer old-access");
    expect(requests[1].url).toBe(apiUrl("/api/auth/refresh"));
    expect(JSON.parse(String(requests[1].init?.body))).toEqual({ refresh_token: "old-refresh" });
    expect(new Headers(requests[2].init?.headers).get("Authorization")).toBe("Bearer new-access");
    expect(storage.get(TOKEN_KEY)).toBe("new-access");
    expect(storage.get(REFRESH_TOKEN_KEY)).toBe("new-refresh");
  });

  it("shares one refresh request between concurrent 401 responses", async () => {
    vi.stubGlobal("localStorage", localStorageMock);
    storage.set(TOKEN_KEY, "old-access");
    storage.set(REFRESH_TOKEN_KEY, "old-refresh");
    let protectedRequests = 0;
    let refreshRequests = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === apiUrl("/api/auth/refresh")) {
        refreshRequests += 1;
        await Promise.resolve();
        return jsonResponse({ access_token: "new-access", refresh_token: "new-refresh" });
      }
      protectedRequests += 1;
      return new Response(null, { status: protectedRequests <= 2 ? 401 : 200 });
    }));

    const [first, second] = await Promise.all([
      apiFetch("/api/chat/message", { headers: authHeaders("old-access") }),
      apiFetch("/api/conversations", { headers: authHeaders("old-access") }),
    ]);

    expect(refreshRequests).toBe(1);
    expect(first.ok).toBe(true);
    expect(second.ok).toBe(true);
  });

  it("reuses a token rotated by another tab before acquiring the refresh lock", async () => {
    vi.stubGlobal("localStorage", localStorageMock);
    storage.set(TOKEN_KEY, "old-access");
    storage.set(REFRESH_TOKEN_KEY, "old-refresh");
    const requests: Array<{ url: string; init?: RequestInit }> = [];

    vi.stubGlobal("navigator", {
      locks: {
        request: vi.fn(async (_name: string, callback: () => Promise<string | null>) => {
          // Simulate another tab completing token rotation while this request waits.
          storage.set(TOKEN_KEY, "rotated-by-other-tab");
          storage.set(REFRESH_TOKEN_KEY, "rotated-refresh");
          return callback();
        }),
      },
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      if (requests.length === 1) return new Response(null, { status: 401 });
      return jsonResponse({ ok: true });
    }));

    const response = await apiFetch("/api/transactions", { headers: authHeaders("old-access") });

    expect(response.status).toBe(200);
    expect(requests).toHaveLength(2);
    expect(requests.some((request) => request.url === apiUrl("/api/auth/refresh"))).toBe(false);
    expect(new Headers(requests[1].init?.headers).get("Authorization")).toBe("Bearer rotated-by-other-tab");
  });

  it("clears stored auth when the refresh token is invalid", async () => {
    vi.stubGlobal("localStorage", localStorageMock);
    storage.set(TOKEN_KEY, "old-access");
    storage.set(REFRESH_TOKEN_KEY, "invalid-refresh");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === apiUrl("/api/auth/refresh")) return jsonResponse({ detail: "Invalid or expired refresh token" }, 401);
      return new Response(null, { status: 401 });
    }));

    const response = await apiFetch("/api/transactions", { headers: authHeaders("old-access") });

    expect(response.status).toBe(401);
    expect(storage.has(TOKEN_KEY)).toBe(false);
    expect(storage.has(REFRESH_TOKEN_KEY)).toBe(false);
  });

  it("does not refresh a public request without an Authorization header", async () => {
    vi.stubGlobal("localStorage", localStorageMock);
    storage.set(REFRESH_TOKEN_KEY, "refresh");
    const fetchMock = vi.fn(async () => new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/auth/login", { method: "POST", headers: authHeaders(null), body: "{}" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("builds JSON headers with optional bearer authentication", () => {
    expect(authHeaders(null)).toEqual({ "Content-Type": "application/json" });
    expect(authHeaders("token")).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer token",
    });
  });

  it("builds multipart headers without forcing a content type", () => {
    expect(authHeadersMultipart(null)).toEqual({});
    expect(authHeadersMultipart("token")).toEqual({ Authorization: "Bearer token" });
  });

  it("removes agent tags and valid JSON metadata from assistant text", () => {
    expect(cleanAssistantText('[AGENT: BUDGET]\n\nReview spending.\n{"intent":"budget_plan"}')).toBe(
      "Review spending.",
    );
    expect(cleanAssistantText("[AGENT: INVESTMENT]\nPrice is {not valid JSON}")).toBe(
      "Price is {not valid JSON}",
    );
  });
});