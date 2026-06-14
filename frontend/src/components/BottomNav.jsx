import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'

// Mobile-only bottom tab bar. The header tab strip is hidden < sm; this replaces
// it with thumb-reachable, ≥44px touch targets so a 4th tab (Usage) fits cleanly.
const TABS = [
  {
    id: 'chat', label: 'Chat',
    icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.86 9.86 0 01-4-.84L3 20l1.34-3.5A7.94 7.94 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
  },
  {
    id: 'suggestions', label: 'Ideas',
    icon: 'M9 18h6M10 22h4M12 2a7 7 0 00-4 12.74V17h8v-2.26A7 7 0 0012 2z',
  },
  {
    id: 'memory', label: 'Memory',
    icon: 'M4 7c0-1.1 3.58-2 8-2s8 .9 8 2-3.58 2-8 2-8-.9-8-2zM4 7v10c0 1.1 3.58 2 8 2s8-.9 8-2V7',
  },
  {
    id: 'usage', label: 'Usage',
    icon: 'M4 19V5M4 19h16M8 19v-6M12 19V9M16 19v-9',
  },
]

export default function BottomNav() {
  const { state, dispatch } = useAppContext()
  const { activeTab } = state

  return (
    <nav className="sm:hidden flex-shrink-0 border-t border-surface-700 bg-surface-900 flex">
      {TABS.map(t => {
        const active = activeTab === t.id
        return (
          <button
            key={t.id}
            onClick={() => dispatch({ type: Actions.SET_ACTIVE_TAB, payload: t.id })}
            aria-label={t.label}
            aria-current={active ? 'page' : undefined}
            className={[
              'flex-1 min-h-[52px] flex flex-col items-center justify-center gap-0.5 transition-colors',
              active ? 'text-indigo-400' : 'text-gray-500 hover:text-gray-300',
            ].join(' ')}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={t.icon} />
            </svg>
            <span className="text-[10px] font-medium">{t.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
