import { useState, useCallback } from 'react'
import { getLogs, analyzeLogs } from '../lib/api'

const LEVEL_STYLES = {
  ERROR: 'text-red-400',
  WARNING: 'text-amber-400',
  WARN: 'text-amber-400',
  INFO: 'text-gray-400',
  DEBUG: 'text-gray-600',
}

function levelClass(line) {
  const up = line.toUpperCase()
  if (up.includes('ERROR')) return LEVEL_STYLES.ERROR
  if (up.includes('WARNING') || up.includes('WARN')) return LEVEL_STYLES.WARNING
  if (up.includes('DEBUG')) return LEVEL_STYLES.DEBUG
  return LEVEL_STYLES.INFO
}

function ErrorCard({ item }) {
  const levelColor = {
    ERROR: 'text-red-400 bg-red-900/20 border-red-800/40',
    WARNING: 'text-amber-400 bg-amber-900/20 border-amber-800/40',
  }[item.level] || 'text-gray-400 bg-surface-800 border-surface-700'

  return (
    <div className={`rounded-xl border p-4 mb-3 ${levelColor}`}>
      <div className="flex items-start gap-2 mb-1 flex-wrap">
        <span className="text-[10px] font-bold uppercase tracking-wide flex-shrink-0 mt-0.5">
          {item.level || 'INFO'}
        </span>
        {item.component && (
          <span className="text-[10px] font-mono bg-black/20 px-1.5 py-0.5 rounded flex-shrink-0">
            {item.component}
          </span>
        )}
        {item.timestamp && (
          <span className="text-[10px] text-gray-500 flex-shrink-0">{item.timestamp}</span>
        )}
      </div>
      <p className="text-sm text-gray-200 mb-2 font-medium">{item.message}</p>
      {item.cause && (
        <div className="mb-1">
          <span className="text-[10px] uppercase tracking-wide text-gray-500 font-semibold">Likely cause: </span>
          <span className="text-xs text-gray-300">{item.cause}</span>
        </div>
      )}
      {item.fix && (
        <div>
          <span className="text-[10px] uppercase tracking-wide text-gray-500 font-semibold">Fix: </span>
          <span className="text-xs text-gray-300">{item.fix}</span>
        </div>
      )}
    </div>
  )
}

export default function LogsTab() {
  const [filter, setFilter] = useState('')
  const [lineCount, setLineCount] = useState(200)
  const [loading, setLoading] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [logData, setLogData] = useState(null)   // { lines, total_matched, log_path, error }
  const [analysis, setAnalysis] = useState(null) // { summary, errors }
  const [showRaw, setShowRaw] = useState(false)
  const [fetchError, setFetchError] = useState('')

  const handleFetch = useCallback(async () => {
    setLoading(true)
    setFetchError('')
    setAnalysis(null)
    try {
      const data = await getLogs(lineCount, filter)
      setLogData(data)
    } catch (e) {
      setFetchError(e.message)
    } finally {
      setLoading(false)
    }
  }, [lineCount, filter])

  const handleAnalyze = useCallback(async () => {
    if (!logData?.lines?.length) return
    setAnalyzing(true)
    try {
      const result = await analyzeLogs(logData.lines)
      setAnalysis(result)
    } catch (e) {
      setAnalysis({ summary: `Analysis failed: ${e.message}`, errors: [] })
    } finally {
      setAnalyzing(false)
    }
  }, [logData])

  const errors = analysis?.errors || []
  const errorCount = errors.filter(e => e.level === 'ERROR').length
  const warnCount = errors.filter(e => e.level === 'WARNING' || e.level === 'WARN').length

  return (
    <div className="flex flex-col h-full overflow-y-auto px-4 py-4 max-w-3xl mx-auto w-full">
      {/* Controls */}
      <div className="bg-surface-900 border border-surface-700 rounded-xl p-4 mb-4">
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-32">
            <label className="text-[10px] text-gray-500 uppercase tracking-wide font-medium block mb-1">
              Filter keyword
            </label>
            <input
              type="text"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="e.g. ERROR, ha_ai_companion…"
              className="w-full bg-surface-800 border border-surface-700 text-gray-200 placeholder-gray-600 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 transition-colors"
            />
          </div>
          <div className="w-24">
            <label className="text-[10px] text-gray-500 uppercase tracking-wide font-medium block mb-1">
              Lines
            </label>
            <select
              value={lineCount}
              onChange={e => setLineCount(Number(e.target.value))}
              className="w-full bg-surface-800 border border-surface-700 text-gray-200 rounded-lg px-2 py-2 text-xs focus:outline-none focus:border-indigo-500 transition-colors"
            >
              <option value={100}>100</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
              <option value={1000}>1000</option>
            </select>
          </div>
          <button
            onClick={handleFetch}
            disabled={loading}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-surface-700 disabled:text-gray-500 text-white rounded-lg text-xs font-medium transition-colors flex-shrink-0"
          >
            {loading ? 'Fetching…' : 'Fetch logs'}
          </button>
        </div>
      </div>

      {fetchError && (
        <div className="text-red-400 text-xs mb-4 bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2">
          {fetchError}
        </div>
      )}

      {/* Log stats + Analyze button */}
      {logData && !logData.error && (
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <span className="text-xs text-gray-400">
            {logData.lines?.length ?? 0} lines shown
            {logData.total_matched != null && logData.total_matched !== logData.lines?.length
              ? ` of ${logData.total_matched} matched`
              : ''}
          </span>
          <span className="text-xs text-gray-600 font-mono truncate">{logData.log_path}</span>
          <div className="ml-auto flex gap-2">
            <button
              onClick={() => setShowRaw(v => !v)}
              className="text-xs px-3 py-1.5 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg transition-colors"
            >
              {showRaw ? 'Hide raw' : 'Raw logs'}
            </button>
            <button
              onClick={handleAnalyze}
              disabled={analyzing}
              className="text-xs px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-surface-700 disabled:text-gray-500 text-white rounded-lg transition-colors font-medium"
            >
              {analyzing ? 'Analyzing…' : 'Analyze with AI'}
            </button>
          </div>
        </div>
      )}

      {logData?.error && (
        <div className="text-red-400 text-xs mb-4 bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2">
          {logData.error}
        </div>
      )}

      {/* AI Analysis */}
      {analysis && (
        <div className="mb-4">
          <div className="flex items-center gap-3 mb-3 flex-wrap">
            <h2 className="text-sm font-semibold text-gray-200">AI Analysis</h2>
            {errorCount > 0 && (
              <span className="text-[10px] px-2 py-0.5 bg-red-900/40 text-red-400 border border-red-800/40 rounded-full font-medium">
                {errorCount} error{errorCount !== 1 ? 's' : ''}
              </span>
            )}
            {warnCount > 0 && (
              <span className="text-[10px] px-2 py-0.5 bg-amber-900/40 text-amber-400 border border-amber-800/40 rounded-full font-medium">
                {warnCount} warning{warnCount !== 1 ? 's' : ''}
              </span>
            )}
          </div>
          {analysis.summary && (
            <p className="text-xs text-gray-300 mb-3 bg-surface-900 border border-surface-700 rounded-lg px-3 py-2">
              {analysis.summary}
            </p>
          )}
          {errors.length === 0 && (
            <p className="text-sm text-emerald-400 italic">No significant issues found.</p>
          )}
          {errors.map((item, i) => (
            <ErrorCard key={i} item={item} />
          ))}
        </div>
      )}

      {/* Raw log viewer */}
      {showRaw && logData?.lines && (
        <div className="bg-surface-950 border border-surface-700 rounded-xl overflow-hidden">
          <div className="px-3 py-2 border-b border-surface-700 text-[10px] text-gray-500 uppercase tracking-wide font-medium">
            Raw log output
          </div>
          <div className="overflow-y-auto max-h-[60vh] p-3">
            {logData.lines.map((line, i) => (
              <div
                key={i}
                className={`text-[11px] font-mono leading-relaxed ${levelClass(line)}`}
              >
                {line || '\u00a0'}
              </div>
            ))}
          </div>
        </div>
      )}

      {!logData && !loading && (
        <p className="text-sm text-gray-500 italic">
          Click "Fetch logs" to load the Home Assistant log file.
        </p>
      )}
    </div>
  )
}
