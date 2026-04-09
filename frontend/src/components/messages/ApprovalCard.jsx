import { useState } from 'react'
import { useAppContext } from '../../store/AppContext'
import { Actions } from '../../store/reducer'
import { submitApproval } from '../../lib/api'

// States: pending | processing | approved | rejected
export default function ApprovalCard({ changeset }) {
  const { dispatch } = useAppContext()
  const [status, setStatus] = useState('pending')
  const [resultData, setResultData] = useState(null)

  if (!changeset) return null

  const handleViewDiff = () => {
    dispatch({ type: Actions.SET_ACTIVE_DIFF, payload: changeset })
  }

  const handleApprove = async () => {
    setStatus('processing')
    try {
      const result = await submitApproval(changeset.changeset_id, true)
      setResultData(result)
      setStatus('approved')
    } catch (e) {
      setStatus('pending')
      dispatch({
        type: Actions.ADD_DISPLAY_MESSAGE,
        payload: { type: 'system', content: `❌ Approval error: ${e.message}` },
      })
    }
  }

  const handleReject = async () => {
    setStatus('processing')
    try {
      await submitApproval(changeset.changeset_id, false)
      setStatus('rejected')
    } catch (e) {
      setStatus('pending')
      dispatch({
        type: Actions.ADD_DISPLAY_MESSAGE,
        payload: { type: 'system', content: `❌ Rejection error: ${e.message}` },
      })
    }
  }

  return (
    <div className="bg-surface-900 border border-surface-700 border-l-4 border-l-indigo-500 rounded-xl p-4 my-2">
      <div className="flex items-start gap-2 mb-2">
        <span className="text-base">📝</span>
        <div>
          <div className="font-semibold text-gray-100 text-sm">Proposed Configuration Changes</div>
          {changeset.reason && (
            <div className="text-gray-400 text-xs mt-0.5">{changeset.reason}</div>
          )}
        </div>
      </div>

      {/* File list */}
      {changeset.file_changes_detail && changeset.file_changes_detail.length > 0 && (
        <div className="mb-3 space-y-1">
          {changeset.file_changes_detail.map((fc, i) => {
            const stat = changeset.diff_stats && changeset.diff_stats[i]
            return (
              <div key={i} className="flex items-center gap-2 text-xs text-gray-400">
                <span className="font-mono text-gray-300 truncate">{fc.file_path}</span>
                {stat && (
                  stat.is_new_file
                    ? <span className="text-indigo-400 flex-shrink-0">new file</span>
                    : <span className="flex-shrink-0">
                        <span className="text-emerald-400">+{stat.added}</span>
                        {' '}
                        <span className="text-red-400">-{stat.removed}</span>
                      </span>
                )}
              </div>
            )
          })}
        </div>
      )}
      {changeset.files && !changeset.file_changes_detail && (
        <div className="mb-3 space-y-1">
          {changeset.files.map((f, i) => (
            <div key={i} className="text-xs font-mono text-gray-400">{f}</div>
          ))}
        </div>
      )}

      {/* State-based content */}
      {status === 'pending' && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleViewDiff}
            className="px-3 py-1.5 text-xs bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors"
          >
            👁 View Diff
          </button>
          <button
            onClick={handleApprove}
            className="px-3 py-1.5 text-xs bg-emerald-700 hover:bg-emerald-600 text-white rounded-lg transition-colors"
          >
            ✓ Approve &amp; Apply
          </button>
          <button
            onClick={handleReject}
            className="px-3 py-1.5 text-xs bg-red-800 hover:bg-red-700 text-white rounded-lg transition-colors"
          >
            ✗ Reject
          </button>
        </div>
      )}

      {status === 'processing' && (
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <div className="flex gap-1">
            <div className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
            <div className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
            <div className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
          Applying changes…
        </div>
      )}

      {status === 'approved' && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-emerald-400 text-sm">
            <span>✅</span>
            <span>Changes applied successfully</span>
          </div>
          {resultData?.reload_triggered && (
            <div className="text-xs text-gray-400">Configuration reloaded.</div>
          )}
          {resultData?.applied_files && resultData.applied_files.length > 0 && (
            <div className="text-xs text-gray-400 space-y-0.5">
              {resultData.applied_files.map((f, i) => (
                <div key={i} className="font-mono">✓ {f}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {status === 'rejected' && (
        <div className="flex items-center gap-2 text-red-400 text-sm">
          <span>✗</span>
          <span>Changes rejected</span>
        </div>
      )}
    </div>
  )
}
