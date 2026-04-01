import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'

export default function Header() {
  const { state, dispatch } = useAppContext()
  const { activeTab } = state

  const version = typeof window !== 'undefined' && window.HA_VERSION ? window.HA_VERSION : ''

  return (
    <header className="bg-surface-900 border-b border-surface-700 px-4 py-3 flex items-center gap-3 flex-shrink-0">
      {/* Hamburger — mobile only */}
      <button
        className="sm:hidden flex-shrink-0 text-gray-400 hover:text-gray-200 transition-colors p-1"
        onClick={() => dispatch({ type: Actions.TOGGLE_SIDEBAR })}
        aria-label="Toggle sidebar"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Logo + title */}
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <img
          src="static/images/icon.png"
          alt="HA AI Companion"
          className="w-7 h-7 flex-shrink-0"
          onError={e => { e.target.style.display = 'none' }}
        />
        <span className="font-semibold text-gray-100 text-sm truncate">HA AI Companion</span>
        {version && (
          <span className="flex-shrink-0 text-xs text-gray-500 bg-surface-800 px-1.5 py-0.5 rounded font-mono">
            v{version}
          </span>
        )}
      </div>

      {/* Tab nav */}
      <div className="flex gap-1 flex-shrink-0 bg-surface-800 rounded-lg p-0.5">
        <button
          className={[
            'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
            activeTab === 'chat'
              ? 'bg-indigo-600 text-white'
              : 'text-gray-400 hover:text-gray-200',
          ].join(' ')}
          onClick={() => dispatch({ type: Actions.SET_ACTIVE_TAB, payload: 'chat' })}
        >
          Chat
        </button>
        <button
          className={[
            'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
            activeTab === 'suggestions'
              ? 'bg-indigo-600 text-white'
              : 'text-gray-400 hover:text-gray-200',
          ].join(' ')}
          onClick={() => dispatch({ type: Actions.SET_ACTIVE_TAB, payload: 'suggestions' })}
        >
          Suggestions
        </button>
        <button
          className={[
            'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
            activeTab === 'logs'
              ? 'bg-indigo-600 text-white'
              : 'text-gray-400 hover:text-gray-200',
          ].join(' ')}
          onClick={() => dispatch({ type: Actions.SET_ACTIVE_TAB, payload: 'logs' })}
        >
          Logs
        </button>
      </div>
    </header>
  )
}
