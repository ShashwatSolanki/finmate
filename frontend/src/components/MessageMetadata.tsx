const METADATA_LABELS: Record<string, string> = {
  rag_chunks_used: "RAG chunks used",
  recent_turns_injected: "Recent turns injected",
  onboarding_injected: "Onboarding injected",
  user_memory_stored: "User memory stored",
  assistant_memory_stored: "Assistant memory stored",
  memory_persisted: "Memory persisted",
  followup_agent_override: "Follow-up agent override",
  income_detected: "Income detected",
  window_days: "Window (days)",
  categories_found: "Categories found",
  source: "Source",
  rag_injected: "RAG injected",
};

type Props = {
  metadata: Record<string, string>;
};

export default function MessageMetadata({ metadata }: Props) {
  const entries = Object.entries(metadata).filter(([key, value]) => value !== "" && key !== "invoice_payload" && key !== "invoice_actions");

  if (entries.length === 0) return null;

  return (
    <details className="message-metadata">
      <summary>Response details</summary>
      <dl>
        {entries.map(([key, value]) => (
          <div key={key} className="message-metadata-row">
            <dt>{METADATA_LABELS[key] ?? key.replace(/_/g, " ")}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
