import { useEffect, useState } from 'react'
import { getUsage } from '../lib/api'

const MODEL_COLORS = ['#818cf8', '#34d399', '#f87171', '#fbbf24', '#22d3ee', '#a78bfa', '#fb923c']
const RANGES = [{ label: '7d', days: 7 }, { label: '30d', days: 30 }, { label: '90d', days: 90 }]

function fmtUsd(n) {
  if (!n) return '$0'
  if (n < 0.01) return '$' + n.toFixed(4)
  return '$' + n.toFixed(2)
}
function fmtTokens(n) {
  if (!n) return '0'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}

// Pick a value to rank rows by — cost when we have it, else input tokens.
function rankVal(b, haveCost) {
  return haveCost ? b.cost_usd : b.input_tokens
}

function BreakdownBar({ title, bucket, haveCost }) {
  const rows = Object.entries(bucket)
    .map(([name, b]) => ({ name, ...b }))
    .sort((a, b) => rankVal(b, haveCost) - rankVal(a, haveCost))
  const total = rows.reduce((s, r) => s + rankVal(r, haveCost), 0) || 1

  return (
    <div className="bg-surface-900 border border-surface-700 rounded-xl p-4">
      <div className="text-sm font-semibold text-gray-200 mb-3">{title}</div>
      {/* Stacked bar */}
      <div className="flex w-full h-3 rounded-full overflow-hidden bg-surface-800 mb-3">
        {rows.map((r, i) => (
          <div
            key={r.name}
            style={{ width: `${(rankVal(r, haveCost) / total) * 100}%`, background: MODEL_COLORS[i % MODEL_COLORS.length] }}
            title={`${r.name}: ${((rankVal(r, haveCost) / total) * 100).toFixed(1)}%`}
          />
        ))}
      </div>
      {/* Rows */}
      <div className="space-y-1.5">
        {rows.map((r, i) => (
          <div key={r.name} className="flex items-center gap-2 text-xs">
            <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }} />
            <span className="font-mono text-gray-300 truncate flex-1" title={r.name}>{r.name || 'unknown'}</span>
            <span className="text-gray-400 tabular-nums">{fmtTokens(r.input_tokens + r.output_tokens)} tok</span>
            {haveCost && <span className="text-emerald-400 tabular-nums w-14 text-right">{fmtUsd(r.cost_usd)}</span>}
            <span className="text-gray-500 tabular-nums w-12 text-right">{((rankVal(r, haveCost) / total) * 100).toFixed(0)}%</span>
          </div>
        ))}
        {rows.length === 0 && <div className="text-xs text-gray-500">No data.</div>}
      </div>
    </div>
  )
}

function DailyBars({ byDay, haveCost }) {
  const days = Object.entries(byDay).map(([d, b]) => ({ d, v: haveCost ? b.cost_usd : b.input_tokens + b.output_tokens, b }))
  const max = Math.max(...days.map(x => x.v), 1)
  return (
    <div className="bg-surface-900 border border-surface-700 rounded-xl p-4">
      <div className="text-sm font-semibold text-gray-200 mb-3">Daily {haveCost ? 'cost' : 'tokens'}</div>
      <div className="flex items-end gap-1 h-28">
        {days.map(({ d, v, b }) => (
          <div key={d} className="flex-1 flex flex-col items-center justify-end group min-w-0" title={`${d}: ${haveCost ? fmtUsd(b.cost_usd) : fmtTokens(b.input_tokens + b.output_tokens)}`}>
            <div className="w-full bg-indigo-500 group-hover:bg-indigo-400 rounded-t transition-colors" style={{ height: `${Math.max((v / max) * 100, 2)}%` }} />
            <span className="text-[9px] text-gray-600 mt-1 rotate-0 truncate w-full text-center">{d.slice(5)}</span>
          </div>
        ))}
        {days.length === 0 && <div className="text-xs text-gray-500">No data.</div>}
      </div>
    </div>
  )
}

export default function UsageTab() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getUsage(days)
      .then(d => { if (!cancelled) { setData(d); setError(null) } })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [days])

  const totals = data?.totals || { input_tokens: 0, cached_tokens: 0, output_tokens: 0, cost_usd: 0, calls: 0 }
  const haveCost = totals.cost_usd > 0
  const cacheRate = totals.input_tokens ? (totals.cached_tokens / totals.input_tokens) * 100 : 0

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-100">Token Usage & Cost</h2>
        <div className="flex gap-1 bg-surface-800 rounded-lg p-0.5">
          {RANGES.map(r => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              className={['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                days === r.days ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-gray-200'].join(' ')}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-sm text-gray-400">Loading usage…</div>}
      {error && <div className="text-sm text-red-400">Failed to load usage: {error}</div>}

      {!loading && !error && (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-surface-900 border border-surface-700 rounded-xl p-3">
              <div className="text-xs text-gray-500">Cost</div>
              <div className="text-lg font-semibold text-emerald-400 tabular-nums">{haveCost ? fmtUsd(totals.cost_usd) : '—'}</div>
            </div>
            <div className="bg-surface-900 border border-surface-700 rounded-xl p-3">
              <div className="text-xs text-gray-500">Input tokens</div>
              <div className="text-lg font-semibold text-gray-200 tabular-nums">{fmtTokens(totals.input_tokens)}</div>
            </div>
            <div className="bg-surface-900 border border-surface-700 rounded-xl p-3">
              <div className="text-xs text-gray-500">Output tokens</div>
              <div className="text-lg font-semibold text-gray-200 tabular-nums">{fmtTokens(totals.output_tokens)}</div>
            </div>
            <div className="bg-surface-900 border border-surface-700 rounded-xl p-3">
              <div className="text-xs text-gray-500">Cache hit</div>
              <div className="text-lg font-semibold text-indigo-300 tabular-nums">{cacheRate.toFixed(0)}%</div>
            </div>
          </div>

          {totals.calls === 0 && (
            <div className="bg-surface-900 border border-surface-700 rounded-xl p-4 text-sm text-gray-400">
              No usage recorded yet in this window. Token/cost data is captured per LLM call once you chat with the agent.
            </div>
          )}

          {totals.calls > 0 && (
            <>
              <BreakdownBar title="By model" bucket={data.by_model} haveCost={haveCost} />
              <BreakdownBar title="By phase (main agent vs. suggestion/summary)" bucket={data.by_phase} haveCost={haveCost} />
              <DailyBars byDay={data.by_day} haveCost={haveCost} />
              {!haveCost && (
                <div className="text-xs text-gray-500">
                  Cost unavailable (provider didn't return it) — ranking by tokens. OpenRouter returns real USD cost automatically.
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
