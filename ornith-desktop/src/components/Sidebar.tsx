import type { Conversation } from '../types';

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
}

export default function Sidebar({ conversations, activeId, onSelect, onNewChat, onDelete }: Props) {
  const ordered = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);

  return (
    <aside className="sidebar">
      {/* Padding for the traffic lights, since the title bar is hidden. */}
      <div className="sidebar-drag" />

      <button className="new-chat-button" onClick={onNewChat}>
        <span aria-hidden="true">+</span> New chat
      </button>

      <nav className="conversation-list">
        {ordered.length === 0 ? (
          <p className="sidebar-empty">No chats yet.</p>
        ) : (
          ordered.map((conversation) => (
            <div
              key={conversation.id}
              className={`conversation-item${conversation.id === activeId ? ' is-active' : ''}`}
            >
              <button
                className="conversation-button"
                onClick={() => onSelect(conversation.id)}
                title={conversation.title}
              >
                {conversation.title}
              </button>
              <button
                className="delete-button"
                onClick={() => onDelete(conversation.id)}
                aria-label={`Delete ${conversation.title}`}
                title="Delete chat"
              >
                ×
              </button>
            </div>
          ))
        )}
      </nav>
    </aside>
  );
}
