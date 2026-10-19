import { useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";

type Props = {
  conversations: { id: string; title: string }[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => Promise<boolean>;
};

export default function ChatSidebar({
  conversations,
  activeId,
  onSelect,
  onNewChat,
  onDelete,
  onRename,
}: Props) {
  const { logout } = useAuth();
  const location = useLocation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  function startRename(id: string, title: string) {
    setEditingId(id);
    setDraftTitle(title);
  }

  async function submitRename(event: FormEvent, id: string) {
    event.preventDefault();
    const title = draftTitle.trim();
    if (!title) return;
    if (await onRename(id, title)) setEditingId(null);
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-brand">
          <span className="brand-mark sm">FM</span>
          <strong>FinMate</strong>
        </div>
        <button type="button" className="btn-new-chat" onClick={onNewChat}>
          + New chat
        </button>
      </div>

      <div className="sidebar-section-label">Recent</div>
      <nav className="conversation-list">
        {conversations.length === 0 && (
          <p className="sidebar-empty">No conversations yet</p>
        )}
        {conversations.map((c) => (
          <div key={c.id} className={`conversation-item ${activeId === c.id ? "active" : ""}`}>
            {editingId === c.id ? (
              <form className="conversation-rename-form" onSubmit={(event) => void submitRename(event, c.id)}>
                <input
                  autoFocus
                  aria-label="Custom chat name"
                  value={draftTitle}
                  maxLength={200}
                  onChange={(event) => setDraftTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setEditingId(null);
                  }}
                />
                <button type="submit" className="conversation-rename-save" title="Save chat name" aria-label="Save chat name">
                  ✓
                </button>
                <button type="button" className="conversation-rename-cancel" title="Cancel rename" aria-label="Cancel rename" onClick={() => setEditingId(null)}>
                  ×
                </button>
              </form>
            ) : (
              <>
                <button type="button" className="conversation-btn" onClick={() => onSelect(c.id)}>
                  {c.title}
                </button>
                <button
                  type="button"
                  className="conversation-rename"
                  title="Rename chat"
                  aria-label={`Rename chat ${c.title}`}
                  onClick={() => startRename(c.id, c.title)}
                >
                  ✎
                </button>
                <button
                  type="button"
                  className="conversation-delete"
                  title="Delete chat"
                  aria-label={`Delete chat ${c.title}`}
                  onClick={() => onDelete(c.id)}
                >
                  ×
                </button>
              </>
            )}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <Link to="/settings" className={`sidebar-link ${location.pathname === "/settings" ? "active" : ""}`}>
          Settings
        </Link>
        <button type="button" className="sidebar-link btn-ghost" onClick={logout}>
          Log out
        </button>
      </div>
    </aside>
  );
}
