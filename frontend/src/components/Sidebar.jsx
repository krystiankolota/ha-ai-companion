import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import { formatSessionDate } from '../lib/utils'

export default function Sidebar({ onNewChat, onSwitchSession, onDeleteSession, onClearAll }) {
  const { state, dispatch } = useAppContext()
  const { sessions, currentSessionId, sidebarOpen } = state

  return (
    <aside
      className={[
        'bg-surface-900 border-r border-surface-700 flex flex-col flex-shrink-0 z-40',
        // Desktop: always visible, fixed width
        'sm:relative sm:translate-x-0 sm:w-[260px]',
        // Mobile: fixed overlay, slides in/out
        'fixed inset-y-0 left-0 w-[260px] transition-transform duration-200',
        sidebarOpen ? 'translate-x-0' : '-translate-x-full sm:translate-x-0',
      ].join(' ')}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700 flex-shrink-0">
        <h2 className="text-sm font-semibold text-gray-200">Conversations</h2>
        <button
          onClick={onNewChat}
          className="text-xs px-2.5 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors"
        >
          + New
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto py-1">
        {sessions.length === 0 ? (
          <div className="px-4 py-3 text-xs text-gray-500">No conversations yet</div>
        ) : (
          sessions.map(session => (
            <SessionItem
              key={session.id}
              session={session}
              isActive={session.id === currentSessionId}
              onSelect={() => onSwitchSession(session.id)}
              onDelete={() => onDeleteSession(session.id)}
            />
          ))
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-surface-700 flex-shrink-0">
        <button
          onClick={onClearAll}
          className="w-full text-xs text-red-500/70 hover:text-red-400 transition-colors text-left"
        >
          Clear All
        </button>
      </div>
    </aside>
  )
}

function SessionItem({ session, isActive, onSelect, onDelete }) {
  return (
    <div
      className={[
        'group flex items-center gap-2 px-3 py-2.5 cursor-pointer transition-colors',
        isActive
          ? 'bg-indigo-900/30 border-l-2 border-l-indigo-500'
          : 'hover:bg-surface-800 border-l-2 border-l-transparent',
      ].join(' ')}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm text-gray-200 truncate">
          {session.title || 'Untitled'}
        </div>
        <div className="text-xs text-gray-500 mt-0.5">
          {session.message_count || 0} msg · {formatSessionDate(session.updated_at)}
        </div>
      </div>
      <button
        className="flex-shrink-0 opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 transition-all text-sm p-1 rounded"
        onClick={e => { e.stopPropagation(); onDelete() }}
        title="Delete"
      >
        🗑
      </button>
    </div>
  )
}
