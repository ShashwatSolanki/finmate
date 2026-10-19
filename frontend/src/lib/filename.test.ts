import { describe, expect, it } from "vitest";
import { sanitizeDownloadBaseName } from "./filename";

describe("sanitizeDownloadBaseName", () => {
  it("uses a custom name and avoids duplicate PDF/CSV extensions", () => {
    expect(sanitizeDownloadBaseName("October expenses.pdf", "invoice-73430")).toBe("October expenses");
    expect(sanitizeDownloadBaseName(" Food.csv ", "invoice-draft")).toBe("Food");
  });

  it("replaces filesystem-reserved characters and trims trailing dots", () => {
    expect(sanitizeDownloadBaseName("../October/food:*?.pdf", "invoice-draft")).toBe("..-October-food---");
    expect(sanitizeDownloadBaseName("Report. ", "invoice-draft")).toBe("Report");
  });

  it("falls back to the default name for empty or whitespace-only input", () => {
    expect(sanitizeDownloadBaseName("", "invoice-draft")).toBe("invoice-draft");
    expect(sanitizeDownloadBaseName("   ", "invoice-73430")).toBe("invoice-73430");
  });

  it("limits names to 100 characters", () => {
    expect(sanitizeDownloadBaseName("a".repeat(120), "fallback")).toHaveLength(100);
  });
});
