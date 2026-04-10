import { useState, useEffect, useCallback } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import {
  generateHealthReport,
  dismissHealthFinding,
  clearHealthDismissed,
  stageHealthFix,
  submitApproval,
} from '../lib/api'

const CATEGORY_META = {
  dead_ref: { label: 'Dead ref', color: 'text-red-400 bg-red-900/30 border-red-800' },
  orphaned_helper: { label: 'Orphaned helper', color: 'text-yellow-400 bg-yellow-900/30 border-yellow-800' },
  duplicate: { label: 'Duplicate', color: 'text-orange-400 bg-orange-900/30 border-orange-800' },
  unused: { label: 'Unused', color: 'text-gray-400 bg-surface-800 border-surface-600' },
}

function categoryLabel(cat) {
  return CATEGORY_META[cat]?.label ?? cat
}
function categoryColor(cat) {
  return CATEGORY_META[cat]?.color ?? 'text-gray-400 bg-surface-800 border-surface-600'
}

// Inline fix-approval modal for pre-computed fixes
function FixModal({ staged, onClose, onApplied }) {
  const [status, setStatus] = useState('pending') // pending | processing | done | error
  const [errorMsg, setErrorMsg] = useState('')

  const handleApprove = async () => {
    setStatus('processing')
    try {
      await submitApproval(staged.changeset_id, true)
      setStatus('done')
      setTimeout(() => { onApplied(); onClose() }, 1200)
    } catch (e) {
      setErrorMsg(e.message)
      setStatus('error')
    }
  }

  const handleCancel = async () => {
    try { await submitApproval(staged.changeset_id, false) } catch { /* ignore */ }
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-surface-900 border border-surface-700 rounded-xl p-5 max-w-lg w-full shadow-2xl">
        <div className="font-semibold text-gray-100 text-sm mb-3">Apply fix — review changes</div>

        {staged.files && staged.files.length > 0 && (
          <div className="mb-4 space-y-1">
            {staged.files.map((f, i) => {
              const stat = staged.diff_stats && staged.diff_stats[i]
              return (
                <div key={i} className="flex items-center gap-2 text-xs text-gray-400">
                  <span className="font-mono text-gray-300 truncate">{f}</span>
                  {stat && !stat.is_new_file && (
                    <span className="flex-shrink-0">
                      <span className="text-emerald-400">+{stat.added}</span>{' '}
                      <span className="text-red-400">-{stat.removed}</span>
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {status === 'pending' && (
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={handleApprove}
              className="px-3 py-1.5 text-xs bg-emerald-700 hover:bg-emerald-600 text-white rounded-lg transition-colors"
            >
              ✓ Approve &amp; Apply
            </button>
            <button
              onClick={handleCancel}
              className="px-3 py-1.5 text-xs bg-surface-700 hover:bg-surface-600 text-gray-300 rounded-lg transition-colors"
            >
              Cancel
            </button>
          </div>
        )}
        {status === 'processing' && (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <div className="flex gap-1">
              {[0, 150, 300].map(d => (
                <div key={d} className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
              ))}
            </div>
            Applying…
          </div>
        )}
        {status === 'done' && (
          <div className="flex items-center gap-2 text-emerald-400 text-sm">
            <span>✅</span><span>Applied successfully</span>
          </div>
        )}
        {status === 'error' && (
          <div className="space-y-2">
            <div className="text-red-400 text-sm">❌ {errorMsg}</div>
            <button
              onClick={handleCancel}
              className="px-3 py-1.5 text-xs bg-surface-700 text-gray-300 rounded-lg"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function FindingCard({ finding, onDismiss, onFix, onStageFix }) {
  const [staging, setStaging] = useState(false)

  const buildFixMessage = (f) => {
    if (f.category === 'dead_ref') {
      const daysPart = f.days_unavailable ? ` (unavailable for ${f.days_unavailable} days)` : ' (missing from registry)'
      return `In ${f.file_path}, the entity ${f.entity_id}${daysPart} is referenced but dead. Please remove or replace this reference.`
    }
    if (f.category === 'duplicate') {
      return `Automations ${f.aliases.join(' and ')} appear to be duplicates: ${f.reason}. Please consolidate them or delete the redundant one.`
    }
    return `Please fix: ${f.category} — ${f.entity_id || f.aliases?.join(', ')}`
  }

  const handleApplyFix = async () => {
    if (!finding.precomputed_fix) return
    setStaging(true)
    try {
      const result = await stageHealthFix(
        finding.precomputed_fix.file_path,
        finding.precomputed_fix.new_content,
      )
      onStageFix({ ...result, findingKey: finding.key })
    } catch (e) {
      alert(`Failed to stage fix: ${e.message}`)
    } finally {
      setStaging(false)
    }
  }

  const title = finding.entity_id || (finding.aliases && finding.aliases.join(' + ')) || finding.key
  const subtitle = [finding.file_path, finding.reason === 'missing' ? 'not in registry' : finding.reason === 'unavailable' ? `unavailable ${finding.days_unavailable}d` : finding.reason].filter(Boolean).join(' · ')
  const files = finding.files ? finding.files.join(', ') : finding.file_path

  return (
    <div className="bg-surface-900 border border-surface-700 rounded-xl p-4">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`text-xs font-medium px-1.5 py-0.5 rounded border ${categoryColor(finding.category)}`}>
              {categoryLabel(finding.category)}
            </span>
            <span className="text-sm text-gray-200 font-mono break-all">{title}</span>
          </div>
          <div className="text-xs text-gray-500 break-all">{files}</div>
          {subtitle && subtitle !== files && (
            <div className="text-xs text-gray-500 mt-0.5">{subtitle}</div>
          )}
          {finding.category === 'duplicate' && finding.reason && (
            <div className="text-xs text-gray-400 mt-1 italic">{finding.reason}</div>
          )}
        </div>

        <div className="flex flex-col gap-1.5 flex-shrink-0">
          {finding.fix === 'precomputed' ? (
            <button
              onClick={handleApplyFix}
              disabled={staging}
              className="px-2.5 py-1 text-xs bg-emerald-800 hover:bg-emerald-700 disabled:opacity-50 text-white rounded-lg transition-colors"
            >
              {staging ? '…' : 'Apply fix'}
            </button>
          ) : (
            <button
              onClick={() => onFix(buildFixMessage(finding))}
              className="px-2.5 py-1 text-xs bg-indigo-700 hover:bg-indigo-600 text-white rounded-lg transition-colors"
            >
              Fix →
            </button>
          )}
          <button
            onClick={() => onDismiss(finding.key)}
            className="px-2.5 py-1 text-xs bg-surface-700 hover:bg-surface-600 text-gray-400 rounded-lg transition-colors"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}

const CATEGORY_ORDER = ['dead_ref', 'orphaned_helper', 'duplicate', 'unused']

export default function HealthTab({ onSendMessage }) {
  const { dispatch } = useAppContext()

  const [findings, setFindings] = useState([])
  const [dismissed, setDismissed] = useState(new Set())
  const [showDismissed, setShowDismissed] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [statusLines, setStatusLines] = useState([])
  const [error, setError] = useState(null)
  const [scannedAt, setScannedAt] = useState(() => {
    try { return localStorage.getItem('health_scanned_at') || null } catch { return null }
  })
  const [stagedFix, setStagedFix] = useState(null) // {changeset_id, files, diff_stats, findingKey}
  const [appliedKeys, setAppliedKeys] = useState(new Set())

  const handleScan = useCallback(async () => {
    setScanning(true)
    setStatusLines([])
    setError(null)
    try {
      const result = await generateHealthReport((msg) => {
        setStatusLines(prev => [...prev, msg])
      })
      if (result) {
        setFindings(result.findings || [])
        setDismissed(new Set(result.dismissed || []))
        const ts = result.scanned_at || new Date().toISOString()
        setScannedAt(ts)
        try { localStorage.setItem('health_scanned_at', ts) } catch { /* ignore */ }
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setScanning(false)
    }
  }, [])

  // Auto-scan on first open if no prior results
  useEffect(() => {
    if (findings.length === 0 && !scannedAt) {
      handleScan()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDismiss = async (key) => {
    try {
      await dismissHealthFinding(key)
      setDismissed(prev => new Set([...prev, key]))
    } catch (e) {
      console.warn('Failed to dismiss:', e)
    }
  }

  const handleClearDismissed = async () => {
    try {
      await clearHealthDismissed()
      setDismissed(new Set())
    } catch (e) {
      console.warn('Failed to clear dismissed:', e)
    }
  }

  const handleFix = (message) => {
    dispatch({ type: Actions.SET_ACTIVE_TAB, payload: 'chat' })
    if (onSendMessage) onSendMessage(message)
  }

  const handleStageFix = (staged) => {
    setStagedFix(staged)
  }

  const handleFixApplied = () => {
    if (stagedFix?.findingKey) {
      setAppliedKeys(prev => new Set([...prev, stagedFix.findingKey]))
    }
    setStagedFix(null)
  }

  const handleRestoreBackup = () => {
    dispatch({ type: Actions.SET_ACTIVE_TAB, payload: 'chat' })
    if (onSendMessage) onSendMessage('List my recent config backups so I can pick one to restore.')
  }

  // Stale warning: > 24h since last scan
  const isStale = scannedAt && (Date.now() - new Date(scannedAt).getTime()) > 86400000

  // Filter and group findings
  const visibleFindings = findings.filter(f =>
    !dismissed.has(f.key) && !appliedKeys.has(f.key)
  )
  const dismissedFindings = findings.filter(f => dismissed.has(f.key))

  const grouped = CATEGORY_ORDER.reduce((acc, cat) => {
    acc[cat] = visibleFindings.filter(f => f.category === cat)
    return acc
  }, {})

  const totalVisible = visibleFindings.length

  const formatTime = (iso) => {
    try {
      return new Date(iso).toLocaleString()
    } catch { return iso }
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto px-4 py-4 max-w-3xl mx-auto w-full">
      {/* Header controls */}
      <div className="bg-surface-900 border border-surface-700 rounded-xl p-4 mb-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="text-sm font-semibold text-gray-100">Config Health Scan</div>
            {scannedAt && !scanning && (
              <div className={`text-xs mt-0.5 ${isStale ? 'text-amber-400' : 'text-gray-500'}`}>
                {isStale ? '⚠ Results may be outdated — ' : ''}
                Last scan: {formatTime(scannedAt)}
              </div>
            )}
          </div>
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={handleRestoreBackup}
              className="px-3 py-1.5 text-xs bg-surface-700 hover:bg-surface-600 border border-surface-600 text-gray-400 rounded-lg transition-colors"
            >
              ↩ Restore backup
            </button>
            <button
              onClick={handleScan}
              disabled={scanning}
              className="px-3 py-1.5 text-xs bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg transition-colors"
            >
              {scanning ? 'Scanning…' : scannedAt ? 'Re-scan' : 'Run scan'}
            </button>
          </div>
        </div>

        {/* Progress */}
        {scanning && statusLines.length > 0 && (
          <div className="mt-3 border-t border-surface-700 pt-3 space-y-0.5">
            {statusLines.map((line, i) => (
              <div key={i} className="text-xs text-gray-400">{line}</div>
            ))}
          </div>
        )}

        {error && (
          <div className="mt-3 text-xs text-red-400">❌ {error}</div>
        )}
      </div>

      {/* Results */}
      {!scanning && scannedAt && (
        <>
          {totalVisible === 0 && dismissedFindings.length === 0 ? (
            <div className="text-center text-gray-500 text-sm py-12">
              ✅ No issues found — config looks clean
            </div>
          ) : (
            <>
              {/* Category sections */}
              {CATEGORY_ORDER.map(cat => {
                const items = grouped[cat]
                if (!items || items.length === 0) return null
                return (
                  <div key={cat} className="mb-5">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-xs font-medium px-1.5 py-0.5 rounded border ${categoryColor(cat)}`}>
                        {categoryLabel(cat)}
                      </span>
                      <span className="text-xs text-gray-500">{items.length} finding{items.length !== 1 ? 's' : ''}</span>
                    </div>
                    <div className="space-y-2">
                      {items.map(f => (
                        <FindingCard
                          key={f.key}
                          finding={f}
                          onDismiss={handleDismiss}
                          onFix={handleFix}
                          onStageFix={handleStageFix}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}

              {/* Dismissed section */}
              {dismissedFindings.length > 0 && (
                <div className="mt-4 border-t border-surface-700 pt-4">
                  <div className="flex items-center justify-between mb-2">
                    <button
                      onClick={() => setShowDismissed(v => !v)}
                      className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
                    >
                      {showDismissed ? '▾' : '▸'} {dismissedFindings.length} dismissed finding{dismissedFindings.length !== 1 ? 's' : ''}
                    </button>
                    {showDismissed && (
                      <button
                        onClick={handleClearDismissed}
                        className="text-xs text-gray-500 hover:text-red-400 transition-colors"
                      >
                        Clear all dismissed
                      </button>
                    )}
                  </div>
                  {showDismissed && (
                    <div className="space-y-1">
                      {dismissedFindings.map(f => (
                        <div key={f.key} className="flex items-center gap-2 text-xs text-gray-600 bg-surface-900 border border-surface-800 rounded-lg px-3 py-2">
                          <span className="flex-1 font-mono break-all">{f.entity_id || f.aliases?.join(' + ') || f.key}</span>
                          <span className="flex-shrink-0">{categoryLabel(f.category)}</span>
                          <button
                            onClick={() => setDismissed(prev => {
                              const next = new Set(prev)
                              next.delete(f.key)
                              return next
                            })}
                            className="text-gray-500 hover:text-gray-300 flex-shrink-0"
                          >
                            restore
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* Pre-computed fix approval modal */}
      {stagedFix && (
        <FixModal
          staged={stagedFix}
          onClose={() => setStagedFix(null)}
          onApplied={handleFixApplied}
        />
      )}
    </div>
  )
}
