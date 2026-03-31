import { useState } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import { clearAllSessions } from '../lib/api'

export default function ClearAllModal({ onDone }) {
  const { state, dispatch } = useAppContext()
  const { showClearAllModal } = state
  const [phase, setPhase] = useState('confirm') // confirm | processing | result
  const [result, setResult] = useState(null)

  const close = () => {
    dispatch({ type: Actions.HIDE_CLEAR_ALL_MODAL })
    setPhase('confirm')
    setResult(null)
  }

  const handleClearAll = async () => {
    setPhase('processing')
    try {
      const data = await clearAllSessions()
      setResult(data)
      setPhase('result')
    } catch (e) {
      setResult({ error: e.message })
      setPhase('result')
    }
  }

  const handleClose = () => {
    if (phase === 'result' && !result?.error) {
      if (typeof onDone === 'function') onDone()
    }
    close()
  }

  if (!showClearAllModal) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-surface-900 border border-surface-700 rounded-2xl w-full max-w-md shadow-2xl">
        {/* Header */}
        <div className="px-5 py-4 border-b border-surface-700">
          <h2 className="text-base font-semibold text-gray-100">Clear All Conversations</h2>
        </div>

        {/* Body */}
        <div className="px-5 py-4">
          {phase === 'confirm' && (
            <p className="text-sm text-gray-300">
              This will analyze all conversations for important facts, save them to memory, then delete all conversation history.
            </p>
          )}

          {phase === 'processing' && (
            <div className="flex items-center gap-3 text-sm text-gray-300">
              <div className="flex gap-1">
                <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              Analyzing conversations…
            </div>
          )}

          {phase === 'result' && result && !result.error && (
            <div className="space-y-2 text-sm text-gray-300">
              <p>Done. Deleted <strong className="text-gray-100">{result.sessions_deleted}</strong> conversation(s).</p>
              {result.memories_saved && result.memories_saved.length > 0 ? (
                <div>
                  <p>Saved {result.memories_saved.length} memory file(s):</p>
                  <ul className="mt-1 space-y-0.5">
                    {result.memories_saved.map((f, i) => (
                      <li key={i} className="text-xs font-mono text-gray-400">• {f}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="text-gray-500">No new memories to save.</p>
              )}
            </div>
          )}

          {phase === 'result' && result?.error && (
            <p className="text-sm text-red-400">Error: {result.error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-surface-700">
          {phase === 'confirm' && (
            <>
              <button
                onClick={close}
                className="px-4 py-2 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg text-sm transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleClearAll}
                className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white rounded-lg text-sm font-medium transition-colors"
              >
                Analyze &amp; Clear All
              </button>
            </>
          )}
          {phase === 'result' && (
            <button
              onClick={handleClose}
              className="px-4 py-2 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg text-sm transition-colors"
            >
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
