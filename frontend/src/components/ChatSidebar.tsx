import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";

type Props = {
  conversations: { id: string; title: string }[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
};

export default function ChatSidebar({
  conversations,
  activeId,
  onSelect,
  onNewChat,
  onDelete,
}: Props) {
  const { logout } = useAuth();
  const location = useLocation();

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
            <button type="button" className="conversation-btn" onClick={() => onSelect(c.id)}>
              {c.title}
            </button>
            <button
              type="button"
              className="conversation-delete"
              title="Delete chat"
              onClick={() => onDelete(c.id)}
            >
              ×
            </button>
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
