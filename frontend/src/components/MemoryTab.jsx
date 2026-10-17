import { useState, useCallback, useEffect } from 'react'
import { getMemories, createMemory, updateMemory, deleteMemory, runMemoryDoctor } from '../lib/api'

const MAX_CHARS = 800
const KINDS = ['preference', 'fact', 'decision', 'procedure']
const VIEWS = [
  { id: 'active', label: 'Active', statuses: ['active'] },
  { id: 'review', label: 'To review', statuses: ['candidate'] },
  { id: 'retired', label: 'Retired', statuses: ['superseded', 'stale', 'rejected'] },
]
const KIND_STYLE = {
  preference: 'bg-indigo-900/40 text-indigo-300',
  fact: 'bg-emerald-900/40 text-emerald-300',
  decision: 'bg-amber-900/40 text-amber-300',
  procedure: 'bg-surface-800 text-gray-400',
}
const BTN = 'text-xs px-2 py-1 rounded transition-colors disabled:opacity-50'
const BTN_GREY = `${BTN} bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300`

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch { return iso }
}

function CharCount({ value }) {
  return (
    <span className={`absolute bottom-2 right-3 text-[10px] ${value.length > MAX_CHARS * 0.9 ? 'text-amber-400' : 'text-gray-600'}`}>
      {value.length}/{MAX_CHARS}
    </span>
  )
}

export default function MemoryTab() {
  const [memories, setMemories] = useState(null)
  const [counts, setCounts] = useState({})
  const [view, setView] = useState('active')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState({})
  const [editing, setEditing] = useState({})   // { id: draft }
  const [creating, setCreating] = useState(false)
  const [newKind, setNewKind] = useState('preference')
  const [newText, setNewText] = useState('')
  const [error, setError] = useState('')
  const [doctor, setDoctor] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getMemories()
      setMemories(data.memories || [])
      setCounts(data.counts || {})
    } catch {
      setMemories([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const act = async (id, fn) => {
    setBusy(prev => ({ ...prev, [id]: true }))
    setError('')
    try {
      await fn()
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(prev => { const n = { ...prev }; delete n[id]; return n })
    }
  }

  const setStatus = (id, status) => act(id, () => updateMemory(id, { status }))

  const saveEdit = (id) => act(id, async () => {
    await updateMemory(id, { text: editing[id] })
    setEditing(prev => { const n = { ...prev }; delete n[id]; return n })
  })

  const remove = (id) => {
    if (!window.confirm(`Delete memory #${id} permanently?`)) return
    act(id, () => deleteMemory(id))
  }

  const handleCreate = () => act('new', async () => {
    if (!newText.trim()) throw new Error('Text is required')
    await createMemory(newKind, newText.trim())
    setCreating(false)
    setNewText('')
  })

  const handleDoctor = () => act('doctor', async () => setDoctor(await runMemoryDoctor()))

  const current = VIEWS.find(v => v.id === view)
  const shown = (memories || []).filter(m => current.statuses.includes(m.status))
  const countFor = v => v.statuses.reduce((n, s) => n + (counts[s] || 0), 0)

  return (
    <div className="flex flex-col h-full overflow-y-auto px-4 py-4 max-w-3xl mx-auto w-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-100 flex items-center gap-2">
            AI Memory
            {!!counts.candidate && (
              <span className="rounded-full bg-amber-900/60 px-1.5 py-0.5 text-[10px] text-amber-300" aria-label={`${counts.candidate} memories to review`}>
                {counts.candidate}
              </span>
            )}
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Facts the AI cannot read from Home Assistant. Only active memories are used in chats.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => { setCreating(true); setError('') }}
            className="text-xs px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white rounded-lg transition-colors">
            + New
          </button>
          <button onClick={handleDoctor} disabled={busy.doctor}
            className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors disabled:opacity-50"
            title="Retire memories that mention entities no longer in Home Assistant">
            {busy.doctor ? 'Checking…' : 'Check health'}
          </button>
          <button onClick={load} disabled={loading}
            className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors disabled:opacity-50">
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="flex gap-1 mb-3" role="tablist">
        {VIEWS.map(v => (
          <button key={v.id} role="tab" aria-selected={view === v.id} onClick={() => setView(v.id)}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${view === v.id ? 'bg-surface-700 text-gray-100' : 'text-gray-500 hover:text-gray-300'}`}>
            {v.label} <span className="text-gray-500">{countFor(v)}</span>
          </button>
        ))}
      </div>

      {doctor && (
        <div className="bg-surface-900 border border-surface-700 rounded-xl px-4 py-3 mb-3 text-xs text-gray-300 flex justify-between gap-3">
          <span>
            {doctor.success === false ? `Health check failed: ${doctor.error}` :
              `${doctor.stale?.length || 0} retired as stale · ${doctor.unverified?.length || 0} unverified · ${doctor.duplicates?.length || 0} duplicate pair(s) · ${doctor.conflicts?.length || 0} conflict pair(s) · ${doctor.unused?.length || 0} unused · ${doctor.expired_candidates?.length || 0} expired candidate(s) · ${doctor.candidates || 0} to review${doctor.note?.startsWith('Entity check skipped') ? ' · entity check skipped (HA lists unavailable)' : ''}`}
          </span>
          <button onClick={() => setDoctor(null)} className="text-gray-500 hover:text-gray-300" aria-label="Dismiss">✕</button>
        </div>
      )}

      {error && <div className="text-xs text-red-400 mb-3">{error}</div>}

      {creating && (
        <div className="bg-surface-900 border border-indigo-600 rounded-xl p-4 mb-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-gray-200">New memory</span>
            <select value={newKind} onChange={e => setNewKind(e.target.value)} aria-label="Kind"
              className="bg-surface-800 border border-surface-700 text-gray-200 rounded-lg px-2 py-1 text-xs">
              {KINDS.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>
          <div className="relative">
            <textarea rows={3} value={newText} maxLength={MAX_CHARS} onChange={e => setNewText(e.target.value)}
              placeholder="One short fact, e.g. 'Always notify both phones for security alerts'"
              className="w-full bg-surface-800 border border-surface-700 text-gray-200 placeholder-gray-600 rounded-lg px-3 py-2 text-xs resize-none focus:outline-none focus:border-indigo-500 transition-colors" />
            <CharCount value={newText} />
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={busy.new}
              className="text-xs px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white rounded-lg transition-colors">
              {busy.new ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => { setCreating(false); setNewText('') }} className={BTN_GREY}>Cancel</button>
          </div>
        </div>
      )}

      {memories === null && <p className="text-xs text-gray-500 italic">Loading…</p>}
      {memories !== null && shown.length === 0 && (
        <p className="text-sm text-gray-500 italic">
          {view === 'review' ? 'Nothing to review.' : view === 'retired' ? 'No retired memories.' :
            'No active memories yet. Tell the AI a preference, or approve items in "To review".'}
        </p>
      )}

      <div className="space-y-2">
        {shown.map(m => {
          const draft = editing[m.id]
          const isEditing = draft !== undefined
          return (
            <div key={m.id} className="bg-surface-900 border border-surface-700 rounded-xl px-4 py-3 space-y-2">
              <div className="flex items-center gap-2 text-[11px] text-gray-500">
                <span className={`px-1.5 py-0.5 rounded ${KIND_STYLE[m.kind] || ''}`}>{m.kind}</span>
                <span className="font-mono">#{m.id}</span>
                {m.key && <span className="font-mono truncate">{m.key}</span>}
                {view === 'retired' && <span className="italic">{m.status}</span>}
                <span className="ml-auto hidden sm:block">{formatDate(m.updated)}</span>
              </div>
              {isEditing ? (
                <div className="relative">
                  <textarea rows={4} value={draft} maxLength={MAX_CHARS}
                    onChange={e => setEditing(prev => ({ ...prev, [m.id]: e.target.value }))}
                    className="w-full bg-surface-800 border border-surface-700 text-gray-200 rounded-lg px-3 py-2 text-xs resize-y focus:outline-none focus:border-indigo-500 transition-colors" />
                  <CharCount value={draft} />
                </div>
              ) : (
                <p className="text-sm text-gray-200 whitespace-pre-wrap break-words">{m.text}</p>
              )}
              <div className="flex gap-2">
                {isEditing ? (
                  <>
                    <button onClick={() => saveEdit(m.id)} disabled={busy[m.id]}
                      className={`${BTN} bg-emerald-700 hover:bg-emerald-600 text-white`}>Save</button>
                    <button onClick={() => setEditing(prev => { const n = { ...prev }; delete n[m.id]; return n })}
                      className={BTN_GREY}>Cancel</button>
                  </>
                ) : (
                  <>
                    {m.status === 'candidate' && (
                      <button onClick={() => setStatus(m.id, 'active')} disabled={busy[m.id]}
                        className={`${BTN} bg-emerald-700 hover:bg-emerald-600 text-white`}>Approve</button>
                    )}
                    {view === 'retired' && (
                      <button onClick={() => setStatus(m.id, 'active')} disabled={busy[m.id]} className={BTN_GREY}>Restore</button>
                    )}
                    <button onClick={() => setEditing(prev => ({ ...prev, [m.id]: m.text }))} className={BTN_GREY}>Edit</button>
                    {view !== 'retired' && (
                      <button onClick={() => setStatus(m.id, 'rejected')} disabled={busy[m.id]}
                        className={`${BTN} text-red-500 hover:text-red-400 hover:bg-red-900/20`}>
                        {m.status === 'candidate' ? 'Reject' : 'Retire'}
                      </button>
                    )}
                    {view === 'retired' && (
                      <button onClick={() => remove(m.id)} disabled={busy[m.id]}
                        className={`${BTN} text-red-500 hover:text-red-400 hover:bg-red-900/20`}>Delete</button>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
