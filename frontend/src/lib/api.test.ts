import { describe, expect, it } from "vitest";
import { authHeaders, authHeadersMultipart, cleanAssistantText } from "./api";

describe("API helpers", () => {
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