import { useState } from 'react'

const MAX_PREVIEW = 500

export default function ToolResultMessage({ functionName, result }) {
  const [open, setOpen] = useState(false)
  const [showAll, setShowAll] = useState(false)

  const success = result?.success !== false
  const json = JSON.stringify(result, null, 2)
  const truncated = json.length > MAX_PREVIEW && !showAll
  const displayJson = truncated ? json.slice(0, MAX_PREVIEW) + '…' : json

  return (
    <div className="bg-surface-800 border border-surface-700 rounded-lg my-1 overflow-hidden">
      <button
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-sm text-gray-400 hover:text-gray-300 hover:bg-surface-750 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-xs transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}>▶</span>
        <span className="flex-1 font-mono">{functionName}</span>
        <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${success ? 'bg-emerald-900/50 text-emerald-400' : 'bg-red-900/50 text-red-400'}`}>
          {success ? 'ok' : 'error'}
        </span>
      </button>
      {open && (
        <div className="border-t border-surface-700 px-3 py-2">
          <pre className="text-[11px] text-gray-400 font-mono overflow-x-auto whitespace-pre-wrap break-words bg-surface-950 rounded p-2">
            <code>{displayJson}</code>
          </pre>
          {json.length > MAX_PREVIEW && (
            <button
              className="text-xs text-indigo-400 hover:text-indigo-300 mt-1"
              onClick={() => setShowAll(s => !s)}
            >
              {showAll ? 'show less' : 'show more'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
