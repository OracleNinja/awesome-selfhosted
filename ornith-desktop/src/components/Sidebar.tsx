import { useCallback, useEffect, useRef, useState } from 'react';
import StatusIndicator from './StatusIndicator';
import { groupConversations } from '../lib/dates';
import type { ConversationSummary, OllamaStatus } from '../../shared/types';

interface Props {
  conversations: ConversationSummary[];
  activeId: string | null;
  status: OllamaStatus | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onOpenSettings: () => void;
  onRetryConnection: () => void;
}

export default function Sidebar({
  conversations,
  activeId,
  status,
  onSelect,
  onNewChat,
  onDelete,
  onRename,
  onOpenSettings,
  onRetryConnection,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId) inputRef.current?.select();
  }, [editingId]);

  const commitRename = useCallback(() => {
    if (!editingId) return;
    const title = draftTitle.trim();
    if (title) onRename(editingId, title);
    setEditingId(null);
  }, [editingId, draftTitle, onRename]);

  const groups = groupConversations(conversations);

  return (
    <aside className="sidebar">
      {/* Reserves room for the macOS traffic lights and gives the window a drag handle. */}
      <div className="sidebar-drag">
        <span className="wordmark">Ornith</span>
      </div>

      <button type="button" className="new-chat-button" onClick={onNewChat} data-testid="new-chat">
        <span aria-hidden="true">+</span> New chat
      </button>

      <nav className="conversation-list" data-testid="conversation-list">
        {groups.length === 0 ? (
          <p className="sidebar-empty">No chats yet.</p>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="conversation-group">
              <div className="group-label">{group.label}</div>

              {group.items.map((conversation) => (
                <div
                  key={conversation.id}
                  className={`conversation-item${conversation.id === activeId ? ' is-active' : ''}`}
                  data-testid="conversation-item"
                >
                  {editingId === conversation.id ? (
                    <input
                      ref={inputRef}
                      className="rename-input"
                      value={draftTitle}
                      onChange={(e) => setDraftTitle(e.target.value)}
                      onBlur={commitRename}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          commitRename();
                        } else if (e.key === 'Escape') {
                          e.preventDefault();
                          // Keep Escape from reaching the app-level handler,
                          // which would otherwise also stop generation.
                          e.stopPropagation();
                          setEditingId(null);
                        }
                      }}
                    />
                  ) : (
                    <button
                      type="button"
                      className="conversation-button"
                      onClick={() => onSelect(conversation.id)}
                      onDoubleClick={() => {
                        setEditingId(conversation.id);
                        setDraftTitle(conversation.title);
                      }}
                      title={conversation.title}
                    >
                      {conversation.title}
                    </button>
                  )}

                  <button
                    type="button"
                    className="delete-button"
                    onClick={() => onDelete(conversation.id)}
                    aria-label={`Delete ${conversation.title}`}
                    title="Delete chat"
                    data-testid="delete-chat"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ))
        )}
      </nav>

      <div className="sidebar-footer">
        <button type="button" className="ghost-button" onClick={onOpenSettings} data-testid="open-settings">
          Settings
        </button>
        <StatusIndicator status={status} onRetry={onRetryConnection} />
      </div>
    </aside>
  );
}
