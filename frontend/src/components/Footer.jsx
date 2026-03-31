import { useAppContext } from '../store/AppContext'

export default function Footer() {
  const { state } = useAppContext()
  const { tokenStats, costUsd } = state
  const hasStats = tokenStats.input > 0 || tokenStats.output > 0

  return (
    <footer className="border-t border-surface-700 px-4 py-2 flex items-center justify-between text-xs text-gray-500 flex-shrink-0 bg-surface-900">
      <span>HA AI Companion</span>
      {hasStats && (
        <div className="flex items-center gap-2">
          <span>in: {tokenStats.input.toLocaleString()}</span>
          <span>out: {tokenStats.output.toLocaleString()}</span>
          {tokenStats.cached > 0 && <span>cache: {tokenStats.cached.toLocaleString()}</span>}
          {costUsd > 0 && (
            <>
              <span className="text-surface-600">|</span>
              <span>${costUsd.toFixed(4)}</span>
            </>
          )}
        </div>
      )}
    </footer>
  )
}
