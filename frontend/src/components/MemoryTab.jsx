import { useState, useCallback, useEffect } from 'react'
import { getMemoryFiles, getMemoryFile, deleteMemoryFile, updateMemoryFile, createMemoryFile } from '../lib/api'

const MAX_CHARS = 800
const FILENAME_RE = /^[a-z0-9_-]+\.md$/

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch { return iso }
}

export default function MemoryTab() {
  const [files, setFiles] = useState(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState({})   // { name: string content }
  const [editing, setEditing] = useState({})      // { name: draft string }
  const [saving, setSaving] = useState({})
  const [deleting, setDeleting] = useState({})
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newContent, setNewContent] = useState('')
  const [createSaving, setCreateSaving] = useState(false)
  const [createError, setCreateError] = useState('')

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
      setEditing(prev => { const n = { ...prev }; delete n[name]; return n })
      return
    }
    try {
      const data = await getMemoryFile(name)
      setExpanded(prev => ({ ...prev, [name]: data.content }))
    } catch {
      setExpanded(prev => ({ ...prev, [name]: '(failed to load)' }))
    }
  }

  const handleStartEdit = (name) => {
    setEditing(prev => ({ ...prev, [name]: expanded[name] || '' }))
  }

  const handleCancelEdit = (name) => {
    setEditing(prev => { const n = { ...prev }; delete n[name]; return n })
  }

  const handleSaveEdit = async (name) => {
    setSaving(prev => ({ ...prev, [name]: true }))
    try {
      await updateMemoryFile(name, editing[name])
      setExpanded(prev => ({ ...prev, [name]: editing[name] }))
      setEditing(prev => { const n = { ...prev }; delete n[name]; return n })
      await load()
    } catch (e) {
      alert(`Save failed: ${e.message}`)
    } finally {
      setSaving(prev => { const n = { ...prev }; delete n[name]; return n })
    }
  }

  const handleDelete = async (name) => {
    if (!window.confirm(`Delete memory file "${name}"?`)) return
    setDeleting(prev => ({ ...prev, [name]: true }))
    try {
      await deleteMemoryFile(name)
      setFiles(prev => prev.filter(f => f.name !== name))
      setExpanded(prev => { const n = { ...prev }; delete n[name]; return n })
      setEditing(prev => { const n = { ...prev }; delete n[name]; return n })
    } catch (e) {
      alert(`Failed to delete: ${e.message}`)
    } finally {
      setDeleting(prev => { const n = { ...prev }; delete n[name]; return n })
    }
  }

  const handleCreate = async () => {
    setCreateError('')
    const name = newName.trim()
    if (!name) { setCreateError('Filename is required'); return }
    const withExt = name.endsWith('.md') ? name : `${name}.md`
    if (!FILENAME_RE.test(withExt)) {
      setCreateError('Use only lowercase letters, numbers, hyphens, underscores (e.g. device_nicknames.md)')
      return
    }
    if (newContent.trim().length > MAX_CHARS) {
      setCreateError(`Content exceeds ${MAX_CHARS} char limit`)
      return
    }
    setCreateSaving(true)
    try {
      const result = await createMemoryFile(withExt, newContent)
      setCreating(false)
      setNewName('')
      setNewContent('')
      await load()
      // Auto-expand the new file
      const data = await getMemoryFile(result.filename)
      setExpanded(prev => ({ ...prev, [result.filename]: data.content }))
    } catch (e) {
      setCreateError(e.message)
    } finally {
      setCreateSaving(false)
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
        <div className="flex gap-2">
          <button
            onClick={() => { setCreating(true); setCreateError('') }}
            className="text-xs px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white rounded-lg transition-colors"
          >
            + New
          </button>
          <button
            onClick={load}
            disabled={loading}
            className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors disabled:opacity-50"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* New file form */}
      {creating && (
        <div className="bg-surface-900 border border-indigo-600 rounded-xl p-4 mb-4 space-y-3">
          <div className="text-xs font-medium text-gray-200">New memory file</div>
          <input
            type="text"
            placeholder="filename.md (e.g. device_nicknames.md)"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            className="w-full bg-surface-800 border border-surface-700 text-gray-200 placeholder-gray-600 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:border-indigo-500 transition-colors"
          />
          <div className="relative">
            <textarea
              placeholder="Memory content (bullet points preferred)…"
              rows={5}
              value={newContent}
              onChange={e => setNewContent(e.target.value)}
              maxLength={MAX_CHARS}
              className="w-full bg-surface-800 border border-surface-700 text-gray-200 placeholder-gray-600 rounded-lg px-3 py-2 text-xs font-mono resize-none focus:outline-none focus:border-indigo-500 transition-colors"
            />
            <span className={`absolute bottom-2 right-3 text-[10px] ${newContent.length > MAX_CHARS * 0.9 ? 'text-amber-400' : 'text-gray-600'}`}>
              {newContent.length}/{MAX_CHARS}
            </span>
          </div>
          {createError && <div className="text-xs text-red-400">{createError}</div>}
          <div className="flex gap-2">
            <button
              onClick={handleCreate}
              disabled={createSaving}
              className="text-xs px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white rounded-lg transition-colors"
            >
              {createSaving ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={() => { setCreating(false); setNewName(''); setNewContent(''); setCreateError('') }}
              className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {files === null && <p className="text-xs text-gray-500 italic">Loading…</p>}

      {files !== null && files.length === 0 && !creating && (
        <p className="text-sm text-gray-500 italic">No memory files yet. The AI will start saving memories as you chat.</p>
      )}

      {files && files.length > 0 && (
        <div className="space-y-2">
          {files.map(f => {
            const isEditing = editing[f.name] !== undefined
            const draft = editing[f.name] ?? ''
            return (
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
                  <div className="border-t border-surface-700 px-4 py-3 space-y-2">
                    {isEditing ? (
                      <>
                        <div className="relative">
                          <textarea
                            rows={8}
                            value={draft}
                            onChange={e => setEditing(prev => ({ ...prev, [f.name]: e.target.value }))}
                            maxLength={MAX_CHARS}
                            className="w-full bg-surface-800 border border-surface-700 text-gray-200 rounded-lg px-3 py-2 text-xs font-mono resize-y focus:outline-none focus:border-indigo-500 transition-colors"
                          />
                          <span className={`absolute bottom-2 right-3 text-[10px] ${draft.length > MAX_CHARS * 0.9 ? 'text-amber-400' : 'text-gray-600'}`}>
                            {draft.length}/{MAX_CHARS}
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleSaveEdit(f.name)}
                            disabled={saving[f.name]}
                            className="text-xs px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white rounded-lg transition-colors"
                          >
                            {saving[f.name] ? 'Saving…' : 'Save'}
                          </button>
                          <button
                            onClick={() => handleCancelEdit(f.name)}
                            className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors"
                          >
                            Cancel
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-words">
                          {expanded[f.name]}
                        </pre>
                        <button
                          onClick={() => handleStartEdit(f.name)}
                          className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors"
                        >
                          Edit
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
