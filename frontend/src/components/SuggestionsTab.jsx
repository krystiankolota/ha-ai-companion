import { useState, useEffect, useCallback, useRef } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import {
  getSuggestions,
  generateSuggestions as apiGenerateSuggestions,
  dismissSuggestion as apiDismissSuggestion,
  getDismissedSuggestions,
  clearDismissedSuggestions,
  restoreDismissedSuggestion,
  getAppliedSuggestions,
  markSuggestionApplied as apiMarkApplied,
  getSuggestionsHistory,
} from '../lib/api'
import { formatGeneratedAt } from '../lib/utils'

const ALL_RESOURCE_TYPES = ['entity_states', 'automations', 'scenes', 'scripts', 'dashboards', 'nodered', 'memory']

const CATEGORY_ICONS = {
  lighting: '💡',
  climate: '🌡️',
  security: '🔒',
  energy: '⚡',
  comfort: '🛋️',
  other: '🤖',
}

function getIcon(category) {
  return CATEGORY_ICONS[category] || CATEGORY_ICONS.other
}

function loadStoredResourceTypes() {
  try {
    const saved = localStorage.getItem('suggestionResourceTypes')
    if (saved) return JSON.parse(saved)
  } catch (_) {}
  return ALL_RESOURCE_TYPES
}

function saveStoredResourceTypes(types) {
  try { localStorage.setItem('suggestionResourceTypes', JSON.stringify(types)) } catch (_) {}
}

function loadStoredFocusPrompt() {
  try { return localStorage.getItem('suggestionFocusPrompt') || '' } catch (_) { return '' }
}

// ── Suggestion Card ────────────────────────────────────────────────────────────

function SuggestionCard({ suggestion, onAddToChat, onDismiss, onMarkApplied, compact }) {
  const [copying, setCopying] = useState(false)

  const copyYaml = async () => {
    if (!suggestion.yaml_block) return
    try {
      await navigator.clipboard.writeText(suggestion.yaml_block)
      setCopying(true)
      setTimeout(() => setCopying(false), 1500)
    } catch {
      setCopying(false)
    }
  }

  const icon = getIcon(suggestion.category)
  const typeBadge = suggestion.type === 'improvement'
    ? <span className="text-[10px] px-1.5 py-0.5 bg-amber-900/50 text-amber-400 rounded font-medium">improvement</span>
    : <span className="text-[10px] px-1.5 py-0.5 bg-indigo-900/50 text-indigo-400 rounded font-medium">new</span>

  if (compact) {
    return (
      <div className="bg-surface-800 border border-surface-700 rounded-lg p-3 my-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span>{icon}</span>
          <span className="text-sm font-medium text-gray-200">{suggestion.title}</span>
        </div>
        <p className="text-xs text-gray-400 mt-1">{suggestion.description}</p>
      </div>
    )
  }

  return (
    <div className="bg-surface-900 border border-surface-700 rounded-xl p-4 my-2">
      <div className="flex items-start gap-2 flex-wrap mb-2">
        <span className="text-base flex-shrink-0 mt-0.5">{icon}</span>
        <span className="text-sm font-semibold text-gray-100 flex-1">{suggestion.title}</span>
        {typeBadge}
        {suggestion.category && (
          <span className="text-[10px] text-gray-500 bg-surface-800 px-1.5 py-0.5 rounded">
            {suggestion.category}
          </span>
        )}
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="text-gray-600 hover:text-gray-400 text-sm ml-auto"
            title="Don't suggest this again"
          >
            ✕
          </button>
        )}
      </div>

      <p className="text-xs text-gray-300 mb-2">{suggestion.description}</p>

      {suggestion.entities && suggestion.entities.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {suggestion.entities.map((e, i) => (
            <span key={i} className="text-[10px] font-mono bg-surface-800 text-gray-400 px-1.5 py-0.5 rounded">{e}</span>
          ))}
        </div>
      )}

      {suggestion.yaml_block && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500 uppercase tracking-wide font-medium">YAML</span>
            <button
              onClick={copyYaml}
              className="text-[10px] text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              {copying ? 'Copied!' : 'Copy'}
            </button>
          </div>
          <pre className="text-[11px] text-gray-300 font-mono bg-surface-950 border border-surface-700 rounded-lg p-2 overflow-x-auto whitespace-pre-wrap break-words">
            {suggestion.yaml_block}
          </pre>
        </div>
      )}

      {suggestion.implementation_hint && !suggestion.yaml_block && (
        <pre className="text-[11px] text-gray-400 font-mono bg-surface-950 border border-surface-700 rounded-lg p-2 overflow-x-auto mb-3 whitespace-pre-wrap break-words">
          {suggestion.implementation_hint}
        </pre>
      )}

      <div className="flex flex-wrap gap-2">
        {onAddToChat && (
          <button
            onClick={onAddToChat}
            className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors"
          >
            Add to chat
          </button>
        )}
        {onMarkApplied && (
          <button
            onClick={onMarkApplied}
            className="text-xs px-3 py-1.5 bg-emerald-900/40 hover:bg-emerald-900/60 text-emerald-400 rounded-lg transition-colors"
          >
            ✓ Applied
          </button>
        )}
      </div>
    </div>
  )
}

// ── Naming Issues ──────────────────────────────────────────────────────────────

function NamingIssuesSection({ issues, onSwitchToChat }) {
  if (!issues || issues.length === 0) return null

  const fixOne = (entity_id, suggested_name) => {
    onSwitchToChat(`Please rename entity ${entity_id} to have the friendly name "${suggested_name}"`)
  }

  const fixAll = () => {
    const lines = issues.map(i => `- ${i.entity_id}: rename to "${i.suggested_name}"`).join('\n')
    onSwitchToChat(`Please rename all these entities to have clearer friendly names:\n${lines}`)
  }

  return (
    <div className="bg-surface-900 border border-surface-700 rounded-xl p-4 mb-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-200">🏷️ Unclear entity names ({issues.length})</h3>
        <button
          onClick={fixAll}
          className="text-xs px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors"
        >
          Fix all in chat
        </button>
      </div>
      <p className="text-xs text-gray-400 mb-3">These entity names may be confusing. Click "Fix in chat" to rename them.</p>
      <div className="space-y-2">
        {issues.map((issue, i) => (
          <div key={i} className="flex items-center gap-2 flex-wrap text-xs bg-surface-800 rounded-lg px-3 py-2">
            <span className="font-mono text-indigo-300">{issue.entity_id}</span>
            <span className="text-gray-400">"{issue.current_name}"</span>
            <span className="text-gray-600">→</span>
            <span className="text-emerald-400">"{issue.suggested_name}"</span>
            {issue.reason && <span className="text-gray-500 flex-1 text-[10px]">{issue.reason}</span>}
            <button
              onClick={() => fixOne(issue.entity_id, issue.suggested_name)}
              className="px-2 py-1 bg-surface-700 hover:bg-surface-600 text-gray-300 rounded transition-colors flex-shrink-0"
            >
              Fix in chat
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Dismissed Section ──────────────────────────────────────────────────────────

function DismissedSection({ dismissedTitles, onClearAll, onRestore, onReloaded }) {
  const [open, setOpen] = useState(false)
  const titles = [...dismissedTitles]
  if (titles.length === 0) return null

  return (
    <div className="mt-4">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-300 transition-colors"
      >
        <span className={open ? 'rotate-90 inline-block transition-transform' : 'inline-block transition-transform'}>▶</span>
        <span>Dismissed ({titles.length})</span>
      </button>
      {open && (
        <div className="mt-2 bg-surface-800 border border-surface-700 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-gray-400">{titles.length} dismissed suggestion{titles.length !== 1 ? 's' : ''}</span>
            <button
              onClick={onClearAll}
              className="text-xs text-red-400 hover:text-red-300 transition-colors"
            >
              Clear all
            </button>
          </div>
          <ul className="space-y-1">
            {titles.map((t, i) => (
              <li key={i} className="flex items-center gap-2 text-xs text-gray-500">
                <span className="flex-1 truncate">{t}</span>
                <button
                  onClick={() => onRestore(t)}
                  className="text-indigo-400 hover:text-indigo-300 flex-shrink-0"
                  title="Restore"
                >
                  ↩
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ── History Section ────────────────────────────────────────────────────────────

function HistorySection({ history, dismissedTitles }) {
  const [open, setOpen] = useState(false)
  const [expandedIdx, setExpandedIdx] = useState(null)
  const past = history.slice(1)
  if (past.length === 0) return null

  return (
    <div className="mt-4">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-300 transition-colors"
      >
        <span className={open ? 'rotate-90 inline-block transition-transform' : 'inline-block transition-transform'}>▶</span>
        <span>History ({past.length})</span>
      </button>
      {open && (
        <div className="mt-2 space-y-1">
          {past.map((entry, i) => {
            const date = formatGeneratedAt(entry.generated_at)
            const subs = (entry.suggestions || []).filter(s => !dismissedTitles.has(s.title))
            const isExpanded = expandedIdx === i
            return (
              <div key={i} className="bg-surface-800 border border-surface-700 rounded-lg overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-3 py-2 text-xs text-gray-400 hover:text-gray-200 transition-colors"
                  onClick={() => setExpandedIdx(isExpanded ? null : i)}
                >
                  <span>{date}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-gray-500">{subs.length} suggestion{subs.length !== 1 ? 's' : ''}</span>
                    <span className={isExpanded ? 'rotate-90 inline-block transition-transform' : 'inline-block transition-transform'}>▶</span>
                  </div>
                </button>
                {isExpanded && (
                  <div className="border-t border-surface-700 p-2">
                    {subs.length === 0 ? (
                      <p className="text-xs text-gray-500 px-2 py-1">No suggestions</p>
                    ) : (
                      subs.map((s, j) => (
                        <SuggestionCard key={j} suggestion={s} compact />
                      ))
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

// ── Main SuggestionsTab ────────────────────────────────────────────────────────

export default function SuggestionsTab() {
  const { dispatch } = useAppContext()

  const [resourceTypes, setResourceTypes] = useState(loadStoredResourceTypes)
  const [focusPrompt, setFocusPrompt] = useState(loadStoredFocusPrompt)
  const [generating, setGenerating] = useState(false)
  const [status, setStatus] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [namingIssues, setNamingIssues] = useState([])
  const [generatedAt, setGeneratedAt] = useState(null)
  const [dismissedTitles, setDismissedTitles] = useState(new Set())
  const [appliedTitles, setAppliedTitles] = useState(new Set())
  const [history, setHistory] = useState([])

  const loadAll = useCallback(async () => {
    const [sugg, dismissed, applied, hist] = await Promise.allSettled([
      getSuggestions(),
      getDismissedSuggestions(),
      getAppliedSuggestions(),
      getSuggestionsHistory(),
    ])
    if (dismissed.status === 'fulfilled') {
      setDismissedTitles(new Set(dismissed.value.dismissed || []))
    }
    if (applied.status === 'fulfilled') {
      setAppliedTitles(new Set(applied.value.applied || []))
    }
    if (sugg.status === 'fulfilled') {
      const data = sugg.value
      setSuggestions(data.suggestions || [])
      setNamingIssues(data.naming_issues || [])
      setGeneratedAt(data.generated_at)
    }
    if (hist.status === 'fulfilled') {
      setHistory(hist.value.history || [])
    }
  }, [])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  const handleToggleResource = (type) => {
    setResourceTypes(prev => {
      const next = prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
      const result = next.length ? next : ALL_RESOURCE_TYPES
      saveStoredResourceTypes(result)
      return result
    })
  }

  const handleFocusPromptChange = (e) => {
    const val = e.target.value.slice(0, 500)
    setFocusPrompt(val)
    try { localStorage.setItem('suggestionFocusPrompt', val) } catch (_) {}
  }

  const handleGenerate = async () => {
    setGenerating(true)
    setStatus('Fetching entity states and generating suggestions…')
    setSuggestions([])
    setNamingIssues([])
    try {
      const types = resourceTypes.length ? resourceTypes : ALL_RESOURCE_TYPES
      const data = await apiGenerateSuggestions(types, focusPrompt || undefined)
      setSuggestions(data.suggestions || [])
      setNamingIssues(data.naming_issues || [])
      setGeneratedAt(data.generated_at)
      setStatus('')
      // Refresh history
      const hist = await getSuggestionsHistory().catch(() => ({ history: [] }))
      setHistory(hist.history || [])
    } catch (e) {
      setStatus(`Error: ${e.message}`)
    } finally {
      setGenerating(false)
    }
  }

  const handleDismiss = async (title) => {
    try {
      await apiDismissSuggestion(title)
      setDismissedTitles(prev => new Set([...prev, title]))
    } catch (e) {
      console.warn('Failed to dismiss:', e)
    }
  }

  const handleMarkApplied = async (title) => {
    try {
      await apiMarkApplied(title)
      setAppliedTitles(prev => new Set([...prev, title]))
    } catch (e) {
      console.warn('Failed to mark applied:', e)
    }
  }

  const handleClearDismissed = async () => {
    try {
      await clearDismissedSuggestions()
      setDismissedTitles(new Set())
    } catch (e) {
      console.warn('Failed to clear dismissed:', e)
    }
  }

  const handleRestore = async (title) => {
    try {
      const remaining = [...dismissedTitles].filter(t => t !== title)
      await clearDismissedSuggestions()
      await Promise.all(remaining.map(t =>
        fetch('api/suggestions/dismiss', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: t }),
        })
      ))
      setDismissedTitles(new Set(remaining))
    } catch (e) {
      console.warn('Failed to restore:', e)
    }
  }

  const switchToChat = useCallback((message) => {
    dispatch({ type: Actions.SET_ACTIVE_TAB, payload: 'chat' })
    // Set prefill via a global ref so ChatInput can pick it up
    // Since we can't directly write to the textarea ref from here, use a custom event
    window.dispatchEvent(new CustomEvent('ha-chat-prefill', { detail: { message } }))
  }, [dispatch])

  const visibleSuggestions = suggestions.filter(
    s => !dismissedTitles.has(s.title) && !appliedTitles.has(s.title)
  )

  const addToChat = (s) => {
    let msg = `Please implement this automation suggestion:\n\n**${s.title}**\n${s.description}`
    if (s.yaml_block) {
      msg += `\n\nStarting YAML:\n\`\`\`yaml\n${s.yaml_block}\n\`\`\``
    }
    switchToChat(msg)
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto px-4 py-4 max-w-3xl mx-auto w-full">
      {/* Controls */}
      <div className="bg-surface-900 border border-surface-700 rounded-xl p-4 mb-4">
        {/* Resource types */}
        <div className="mb-3">
          <div className="text-xs text-gray-400 font-medium mb-2">Include in analysis:</div>
          <div className="flex flex-wrap gap-2">
            {ALL_RESOURCE_TYPES.map(type => (
              <label key={type} className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={resourceTypes.includes(type)}
                  onChange={() => handleToggleResource(type)}
                  className="accent-indigo-500"
                />
                <span className="text-xs text-gray-300">{type}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Focus prompt */}
        <div className="mb-3">
          <textarea
            className="w-full bg-surface-800 border border-surface-700 text-gray-200 placeholder-gray-600 rounded-lg px-3 py-2 text-xs resize-none focus:outline-none focus:border-indigo-500 transition-colors"
            placeholder="Optional: focus area (e.g. 'energy saving', 'security')…"
            rows={2}
            value={focusPrompt}
            onChange={handleFocusPromptChange}
            maxLength={500}
          />
        </div>

        <button
          onClick={handleGenerate}
          disabled={generating}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-surface-700 disabled:text-gray-500 text-white rounded-lg text-xs font-medium transition-colors"
        >
          {generating ? 'Generating…' : 'Generate suggestions'}
        </button>
      </div>

      {/* Status */}
      {status && (
        <div className="text-xs text-gray-400 mb-3 italic">{status}</div>
      )}

      {/* Generated at */}
      {generatedAt && !generating && (
        <div className="text-xs text-gray-500 mb-3">
          Last generated: {formatGeneratedAt(generatedAt)}
          {suggestions.length - visibleSuggestions.length > 0 && (
            <span className="ml-1">({suggestions.length - visibleSuggestions.length} hidden)</span>
          )}
        </div>
      )}

      {/* Naming issues */}
      <NamingIssuesSection issues={namingIssues} onSwitchToChat={switchToChat} />

      {/* Suggestions list */}
      {visibleSuggestions.length === 0 && !generating && !status && (
        <p className="text-sm text-gray-500 italic">
          No suggestions yet. Click "Generate suggestions" to get started.
        </p>
      )}
      {visibleSuggestions.map((s, i) => (
        <SuggestionCard
          key={s.title || i}
          suggestion={s}
          onAddToChat={() => addToChat(s)}
          onDismiss={() => handleDismiss(s.title)}
          onMarkApplied={() => handleMarkApplied(s.title)}
        />
      ))}

      {/* Dismissed */}
      <DismissedSection
        dismissedTitles={dismissedTitles}
        onClearAll={handleClearDismissed}
        onRestore={handleRestore}
      />

      {/* History */}
      <HistorySection history={history} dismissedTitles={dismissedTitles} />
    </div>
  )
}
