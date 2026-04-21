import { useState } from 'react'

const STATUS_ICON = {
  pending: <span className="inline-block w-3 h-3 rounded-full border border-gray-500 opacity-50" />,
  running: (
    <svg className="w-3 h-3 animate-spin text-indigo-400" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  ),
  done: <span className="text-emerald-400 text-xs">✓</span>,
  error: <span className="text-red-400 text-xs">✗</span>,
}

function StepRow({ step, isLast }) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="flex-shrink-0 w-4 flex items-center justify-center">
        {STATUS_ICON[step.status] ?? STATUS_ICON.pending}
      </span>
      <span className={`text-xs truncate ${step.status === 'error' ? 'text-red-400' : step.status === 'done' ? 'text-gray-300' : 'text-gray-400'}`}>
        {step.label}
        {step.argsSummary && (
          <span className="text-gray-500 ml-1">· {step.argsSummary}</span>
        )}
        {step.status === 'error' && step.resultSummary && (
          <span className="text-red-400 ml-1">· {step.resultSummary}</span>
        )}
        {step.status === 'done' && step.resultSummary && step.resultSummary !== 'ok' && (
          <span className="text-gray-500 ml-1">· {step.resultSummary}</span>
        )}
      </span>
    </div>
  )
}

function DetailStep({ step, index }) {
  const [open, setOpen] = useState(false)
  const hasDetail = step.args || step.result

  return (
    <div className="border border-surface-700 rounded overflow-hidden">
      <button
        className="w-full flex items-center gap-2 px-2 py-1.5 text-left hover:bg-surface-750 transition-colors"
        onClick={() => hasDetail && setOpen(o => !o)}
        disabled={!hasDetail}
      >
        <span className="w-4 flex items-center justify-center flex-shrink-0">
          {STATUS_ICON[step.status] ?? STATUS_ICON.pending}
        </span>
        <span className="font-mono text-xs text-gray-400 flex-1 truncate">{step.name}</span>
        {step.argsSummary && (
          <span className="text-gray-500 text-xs truncate max-w-[200px]">{step.argsSummary}</span>
        )}
        <span className={`text-xs px-1 py-0.5 rounded flex-shrink-0 ${
          step.status === 'error' ? 'bg-red-900/50 text-red-400' :
          step.status === 'done'  ? 'bg-emerald-900/40 text-emerald-400' :
          step.status === 'running' ? 'bg-indigo-900/40 text-indigo-400' :
          'text-gray-600'
        }`}>
          {step.status === 'done' ? (step.resultSummary || 'ok') : step.status}
        </span>
        {hasDetail && (
          <span className="text-gray-600 text-xs flex-shrink-0">{open ? '▲' : '▼'}</span>
        )}
      </button>

      {open && (
        <div className="border-t border-surface-700 px-2 py-2 space-y-2">
          {step.args && (
            <div>
              <div className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Args</div>
              <pre className="text-[11px] text-gray-400 font-mono bg-surface-950 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words">
                {JSON.stringify(step.args, null, 2)}
              </pre>
            </div>
          )}
          {step.result && (
            <div>
              <div className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Result</div>
              <pre className="text-[11px] text-gray-400 font-mono bg-surface-950 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words">
                {JSON.stringify(step.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function BreadcrumbsMessage({ steps }) {
  const [showDetails, setShowDetails] = useState(false)

  if (!steps || steps.length === 0) return null

  const hasAnyDetail = steps.some(s => s.args || s.result)

  return (
    <div className="my-1">
      {/* Layer 1 — compact breadcrumb row */}
      <div className="flex items-start gap-1 flex-wrap">
        <div className="flex flex-col gap-0.5 flex-1 min-w-0 py-0.5">
          {steps.map((step, i) => (
            <StepRow key={step.tool_call_id || i} step={step} isLast={i === steps.length - 1} />
          ))}
        </div>
        {hasAnyDetail && (
          <button
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors flex-shrink-0 py-0.5 mt-0.5"
            onClick={() => setShowDetails(d => !d)}
          >
            {showDetails ? 'hide details' : 'show details'}
          </button>
        )}
      </div>

      {/* Layer 2 — expandable detail panel */}
      {showDetails && (
        <div className="mt-1.5 space-y-1">
          {steps.map((step, i) => (
            <DetailStep key={step.tool_call_id || i} step={step} index={i} />
          ))}
        </div>
      )}
    </div>
  )
}
