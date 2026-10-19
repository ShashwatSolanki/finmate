/** Create a safe, portable download filename base (without PDF/CSV extension). */
export function sanitizeDownloadBaseName(value: string, fallback: string): string {
  const clean = (input: string) =>
    input
      .trim()
      .replace(/\.(?:pdf|csv)$/i, "")
      .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
      .replace(/\s+/g, " ")
      .replace(/[. ]+$/g, "")
      .slice(0, 100);

  return clean(value) || clean(fallback) || "invoice";
}
