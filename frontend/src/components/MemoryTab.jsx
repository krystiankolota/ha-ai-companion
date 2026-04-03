import { useState, useCallback, useEffect } from 'react'
import { getMemoryFiles, getMemoryFile, deleteMemoryFile } from '../lib/api'

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch { return iso }
}

export default function MemoryTab() {
  const [files, setFiles] = useState(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState({})
  const [deleting, setDeleting] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getMemoryFiles()
      setFiles(data.files || [])
    } catch {
      setFiles([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleExpand = async (name) => {
    if (expanded[name] !== undefined) {
      setExpanded(prev => { const n = { ...prev }; delete n[name]; return n })
      return
    }
    try {
      const data = await getMemoryFile(name)
      setExpanded(prev => ({ ...prev, [name]: data.content }))
    } catch {
      setExpanded(prev => ({ ...prev, [name]: '(failed to load)' }))
    }
  }

  const handleDelete = async (name) => {
    if (!window.confirm(`Delete memory file "${name}"?`)) return
    setDeleting(prev => ({ ...prev, [name]: true }))
    try {
      await deleteMemoryFile(name)
      setFiles(prev => prev.filter(f => f.name !== name))
      setExpanded(prev => { const n = { ...prev }; delete n[name]; return n })
    } catch (e) {
      alert(`Failed to delete: ${e.message}`)
    } finally {
      setDeleting(prev => { const n = { ...prev }; delete n[name]; return n })
    }
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto px-4 py-4 max-w-3xl mx-auto w-full">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-100">AI Memory</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            What the AI remembers about your home across sessions — stored in <span className="font-mono">.ai_agent_memories/</span>
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors disabled:opacity-50"
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {files === null && (
        <p className="text-xs text-gray-500 italic">Loading…</p>
      )}

      {files !== null && files.length === 0 && (
        <p className="text-sm text-gray-500 italic">No memory files yet. The AI will start saving memories as you chat.</p>
      )}

      {files && files.length > 0 && (
        <div className="space-y-2">
          {files.map(f => (
            <div key={f.name} className="bg-surface-900 border border-surface-700 rounded-xl overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3">
                <button
                  className="flex items-center gap-2 flex-1 text-left min-w-0"
                  onClick={() => handleExpand(f.name)}
                >
                  <span className={`text-[10px] text-gray-500 transition-transform flex-shrink-0 ${expanded[f.name] !== undefined ? 'rotate-90' : ''}`}>▶</span>
                  <span className="font-mono text-sm text-gray-200 truncate">{f.name}</span>
                </button>
                <span className="text-xs text-gray-600 flex-shrink-0">{f.chars} chars</span>
                <span className="text-xs text-gray-600 flex-shrink-0 hidden sm:block">{formatDate(f.updated)}</span>
                <button
                  onClick={() => handleDelete(f.name)}
                  disabled={deleting[f.name]}
                  className="text-xs px-2 py-1 text-red-500 hover:text-red-400 hover:bg-red-900/20 rounded transition-colors flex-shrink-0 disabled:opacity-50"
                  title="Delete"
                >
                  {deleting[f.name] ? '…' : 'Delete'}
                </button>
              </div>
              {expanded[f.name] !== undefined && (
                <div className="border-t border-surface-700 px-4 py-3">
                  <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-words">
                    {expanded[f.name]}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
