import { useCallback, useEffect, useState } from 'react'
import { getAppliedChangesets, getChangeset, restoreChangeset } from '../lib/api'
import ApprovalCard from './messages/ApprovalCard'

const BTN = 'px-3 py-1.5 text-xs rounded-lg transition-colors disabled:opacity-50'

function ItemSummary({ items }) {
  if (!items) return null
  const parts = [['added', 'text-emerald-400', '+'], ['changed', 'text-amber-300', '~'], ['removed', 'text-red-400', '−']]
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs mt-0.5">
      {parts.flatMap(([k, cls, sign]) => (items[k] || []).map(name => (
        <span key={k + name} className={cls}>{sign} {name}</span>
      )))}
    </div>
  )
}

// Applied changesets, newest first. Restore stages a new changeset that goes
// through the normal approval card (diff first, then apply).
export default function ChangesTab() {
  const [list, setList] = useState(null)
  const [error, setError] = useState('')
  const [staged, setStaged] = useState({})   // applied id -> restore changeset
  const [busy, setBusy] = useState({})

  const load = useCallback(async () => {
    setError('')
    try {
      setList((await getAppliedChangesets(30)).changesets || [])
    } catch (e) {
      setError(e.message)
      setList([])
    }
  }, [])

  useEffect(() => { load() }, [load])

  const restore = async (id) => {
    setBusy(b => ({ ...b, [id]: true }))
    try {
      const res = await restoreChangeset(id)
      const cs = await getChangeset(res.changeset_id).catch(() => null)
      setStaged(s => ({ ...s, [id]: { ...res, reason: res.message, file_changes_detail: cs?.file_changes } }))
    } catch (e) {
      setStaged(s => ({ ...s, [id]: { error: e.message } }))
    } finally {
      setBusy(b => ({ ...b, [id]: false }))
    }
  }

  return (
    <div className="h-full overflow-y-auto p-4 space-y-3 max-w-3xl w-full mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-100">Applied changes</h2>
          <p className="text-xs text-gray-500">Every approved change keeps a backup. Restore shows the diff before anything is written.</p>
        </div>
        <button onClick={load} className={`${BTN} bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300`}>↻ Refresh</button>
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}
      {list === null && <div className="text-xs text-gray-500">Loading…</div>}
      {list && list.length === 0 && !error && <div className="text-xs text-gray-500">No applied changes yet.</div>}

      {list && list.map(cs => {
        const st = staged[cs.id]
        const restorable = cs.files.some(f => f.restorable)
        return (
          <div key={cs.id} className="bg-surface-900 border border-surface-700 rounded-xl p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs text-gray-400">
                  <span className="font-mono text-gray-300">{cs.id}</span>
                  {' · '}{new Date(cs.applied_at).toLocaleString()}
                  {cs.restored_from && <span className="text-indigo-300"> · restore of {cs.restored_from}</span>}
                </div>
                {cs.files.map(f => (
                  <div key={f.file_path} className="mt-1">
                    <span className="font-mono text-xs text-gray-200">{f.file_path}</span>
                    {!f.restorable && <span className="text-xs text-gray-500"> (new file — remove manually)</span>}
                    <ItemSummary items={f.items} />
                  </div>
                ))}
              </div>
              {!st?.changeset_id && (
                <button
                  onClick={() => restore(cs.id)}
                  disabled={!restorable || busy[cs.id]}
                  className={`${BTN} flex-shrink-0 bg-amber-700 hover:bg-amber-600 text-white`}
                >
                  {busy[cs.id] ? 'Preparing…' : '↺ Restore'}
                </button>
              )}
            </div>
            {st?.error && <div className="text-xs text-red-400 mt-2">{st.error}</div>}
            {st?.warnings && st.warnings.map(w => <div key={w} className="text-xs text-amber-400 mt-2">⚠️ {w}</div>)}
            {st?.changeset_id && <ApprovalCard changeset={st} />}
          </div>
        )
      })}
    </div>
  )
}
