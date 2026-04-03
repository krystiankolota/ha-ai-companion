import { useState, useCallback } from 'react'
import { getMemoryFiles, getMemoryFile, deleteMemoryFile } from '../lib/api'

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch { return iso }
}

export default function MemoryViewer() {
  const [open, setOpen] = useState(false)
  const [files, setFiles] = useState(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState({}) // filename -> content | null
  const [deleting, setDeleting] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getMemoryFiles()
      setFiles(data.files || [])
    } catch (e) {
      setFiles([])
    } finally {
      setLoading(false)
    }
  }, [])

  const handleOpen = () => {
    const next = !open
    setOpen(next)
    if (next && files === null) load()
  }

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
    <div className="mt-4">
      <button
        onClick={handleOpen}
        className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-300 transition-colors"
      >
        <span className={open ? 'rotate-90 inline-block transition-transform' : 'inline-block transition-transform'}>▶</span>
        <span>Memory files {files !== null ? `(${files.length})` : ''}</span>
        {!open && (
          <span className="text-[10px] text-gray-600 ml-1">— what the AI remembers about your home</span>
        )}
      </button>

      {open && (
        <div className="mt-2 bg-surface-800 border border-surface-700 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-gray-400">
              {files === null ? 'Loading…' : `${files.length} file${files.length !== 1 ? 's' : ''} in .ai_agent_memories/`}
            </span>
            <button
              onClick={load}
              className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
              disabled={loading}
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>

          {files && files.length === 0 && (
            <p className="text-xs text-gray-500 italic">No memory files yet.</p>
          )}

          {files && files.length > 0 && (
            <div className="space-y-1">
              {files.map(f => (
                <div key={f.name} className="bg-surface-900 border border-surface-700 rounded-lg overflow-hidden">
                  <div className="flex items-center gap-2 px-3 py-2 text-xs">
                    <button
                      className="flex items-center gap-1 flex-1 text-left text-gray-300 hover:text-gray-100 transition-colors min-w-0"
                      onClick={() => handleExpand(f.name)}
                    >
                      <span className={`text-[10px] transition-transform flex-shrink-0 ${expanded[f.name] !== undefined ? 'rotate-90' : ''}`}>▶</span>
                      <span className="font-mono truncate">{f.name}</span>
                    </button>
                    <span className="text-gray-600 flex-shrink-0">{f.chars} chars</span>
                    <span className="text-gray-600 flex-shrink-0 hidden sm:inline">{formatDate(f.updated)}</span>
                    <button
                      onClick={() => handleDelete(f.name)}
                      disabled={deleting[f.name]}
                      className="text-red-500 hover:text-red-400 transition-colors flex-shrink-0 ml-1"
                      title="Delete"
                    >
                      {deleting[f.name] ? '…' : '🗑'}
                    </button>
                  </div>
                  {expanded[f.name] !== undefined && (
                    <div className="border-t border-surface-700 px-3 py-2">
                      <pre className="text-[11px] text-gray-300 font-mono whitespace-pre-wrap break-words overflow-x-auto">
                        {expanded[f.name]}
                      </pre>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
