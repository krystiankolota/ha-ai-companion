import { useEffect, useState } from 'react'
import { getModelAdvice } from '../lib/api'

const ROLE_LABELS = { main: 'Main', fast: 'Fast' }
const RANK_INFO = [
  { key: 'value', title: 'Best value (main model)', hint: 'Best agent score per dollar among strong models.' },
  { key: 'quality', title: 'Highest quality', hint: 'Best agentic / coding score; price breaks ties.' },
  { key: 'fast', title: 'Fast model (side tasks)', hint: 'Cheapest model that is still capable.' },
]

function usd(n) {
  if (n == null) return '—'
  if (n < 0.01) return '$' + n.toFixed(4)
  return '$' + n.toFixed(3)
}
function when(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : 'never'
}
function score(v) {
  return v == null ? <span className="text-gray-600">missing</span> : v.toFixed(1)
}

function ModelRow({ m }) {
  return (
    <tr className="border-t border-surface-800">
      <td className="py-1 pr-2 font-mono text-gray-300 truncate max-w-[14rem]" title={m.id}>{m.id}</td>
      <td className="py-1 px-2 text-right tabular-nums">{score(m.intelligence)}</td>
      <td className="py-1 px-2 text-right tabular-nums">{score(m.agentic)}</td>
      <td className="py-1 px-2 text-right tabular-nums text-gray-400">${m.price_in_per_m} / ${m.price_out_per_m}</td>
      <td className="py-1 px-2 text-right tabular-nums text-emerald-400">{usd(m.est_cost_per_session)}</td>
      <td className="py-1 pl-2 text-right tabular-nums text-gray-400">{m.sessions_per_10usd ?? '—'}</td>
    </tr>
  )
}

function ModelTable({ rows }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-gray-500">
          <th className="text-left font-normal pb-1">Model</th>
          <th className="text-right font-normal pb-1 px-2">Intel.</th>
          <th className="text-right font-normal pb-1 px-2">Agentic</th>
          <th className="text-right font-normal pb-1 px-2">$/M in / out</th>
          <th className="text-right font-normal pb-1 px-2">Est. / session</th>
          <th className="text-right font-normal pb-1">Sessions / $10</th>
        </tr>
      </thead>
      <tbody>{rows.map(m => <ModelRow key={m.id} m={m} />)}</tbody>
    </table>
  )
}

export default function ModelAdvisor() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = (refresh = false) => {
    setLoading(true)
    getModelAdvice(refresh)
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const src = data?.sources || {}
  const hasScores = data?.scored_models > 0

  return (
    <div className="bg-surface-900 border border-surface-700 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-gray-200">Model advisor</div>
        <button onClick={() => load(true)} disabled={loading}
          className="text-xs px-2 py-1 rounded-md bg-surface-800 text-gray-300 hover:text-white disabled:opacity-50">
          {loading ? 'Loading…' : 'Refresh data'}
        </button>
      </div>
      {error && <div className="text-xs text-red-400">Failed to load advice: {error}</div>}

      {data && (
        <>
          <div className="space-y-1.5">
            {data.configured.map(c => (
              <div key={c.role} className="text-xs">
                <span className="text-gray-500 w-20 inline-block">{ROLE_LABELS[c.role] || c.role}</span>
                <span className="font-mono text-gray-300">{c.model}</span>
                {c.info && <span className="text-gray-500"> · {usd(c.info.est_cost_per_session)} / session</span>}
                {c.issues.map(i => <div key={i} className="text-amber-400 ml-20">⚠ {i}</div>)}
              </div>
            ))}
          </div>

          {data.observed?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-gray-300">Your usage (last 30 days)</div>
              <div className="text-[11px] text-gray-500 mb-1">Real chat cost and reliability per model from your own sessions. Success = turn finished without error or iteration limit. Compare with the estimates below.</div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr className="text-gray-500 text-left">
                    <th className="font-normal pr-3">Model</th><th className="font-normal pr-3">Sessions</th>
                    <th className="font-normal pr-3">Calls/session</th><th className="font-normal pr-3">$/session</th>
                    <th className="font-normal pr-3">Cache hit</th>
                    <th className="font-normal pr-3">Success</th><th className="font-normal">Tool errors/turn</th>
                  </tr></thead>
                  <tbody>
                    {data.observed.map(o => (
                      <tr key={o.model} className="text-gray-300">
                        <td className="font-mono pr-3">{o.model}</td><td className="pr-3">{o.sessions}</td>
                        <td className="pr-3">{o.calls_per_session}</td><td className="pr-3">{usd(o.cost_per_session)}</td>
                        <td className="pr-3">{o.cache_hit == null ? '—' : `${Math.round(o.cache_hit * 100)}%`}</td>
                        <td className="pr-3">{o.success_rate == null ? '—' : `${Math.round(o.success_rate * 100)}% of ${o.turns}`}</td>
                        <td>{o.tool_errors_per_turn ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {hasScores ? RANK_INFO.map(r => (
            <div key={r.key}>
              <div className="text-xs font-semibold text-gray-300">{r.title}</div>
              <div className="text-[11px] text-gray-500 mb-1">{r.hint}</div>
              <ModelTable rows={data.rankings[r.key]} />
            </div>
          )) : (
            <div className="text-xs text-gray-400">
              No quality scores available yet. Click Refresh data, or add an optional Artificial Analysis API key
              (<span className="font-mono">artificial_analysis_api_key</span>) to score more of the {data.candidate_models} tool-capable models.
            </div>
          )}

          <div className="text-[11px] text-gray-500 space-y-0.5">
            <div>
              Cost estimate uses {data.mix.from_history ? 'your last 30 days' : 'a typical turn (no history yet)'}:
              {' '}{Math.round(data.mix.input).toLocaleString()} input ({Math.round(data.mix.cached).toLocaleString()} cached),
              {' '}{Math.round(data.mix.output).toLocaleString()} output tokens per call, {data.mix.calls_per_session.toFixed(1)} calls per session.
            </div>
            <div>
              Prices: OpenRouter ({src.openrouter?.source}, {when(src.openrouter?.fetched_at)}).
              Scores: Artificial Analysis via OpenRouter ({src.catalog_scores ?? 0} models)
              {src.artificial_analysis?.source !== 'no-key' && ` + AA API (${src.artificial_analysis?.source}${src.artificial_analysis?.fetched_at ? ', ' + when(src.artificial_analysis.fetched_at) : ''})`}.
              {data.scored_models > 0 && ` ${data.scored_models} of ${data.candidate_models} models matched a score; the rest show as missing.`}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
