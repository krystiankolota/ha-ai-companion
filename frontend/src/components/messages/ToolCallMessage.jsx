import { useState } from 'react'

export default function ToolCallMessage({ toolNames, toolCalls }) {
  const [open, setOpen] = useState(false)

  const names = toolNames || (toolCalls || []).map(tc => tc.function.name)
  const summary = `Tool call: ${names.join(', ')}`

  return (
    <div className="bg-surface-800 border border-surface-700 rounded-lg my-1 overflow-hidden">
      <button
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-sm text-gray-400 hover:text-gray-300 hover:bg-surface-750 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-xs transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}>▶</span>
        <span className="flex-1 font-mono truncate">{summary}</span>
      </button>
      {open && toolCalls && (
        <div className="border-t border-surface-700 px-3 py-2 space-y-2">
          {toolCalls.map((tc, i) => {
            let args
            try {
              args = typeof tc.function.arguments === 'string'
                ? JSON.parse(tc.function.arguments)
                : tc.function.arguments
            } catch {
              args = tc.function.arguments
            }
            return (
              <div key={tc.id || i} className="text-xs">
                <div className="font-medium text-gray-300 mb-1">{i + 1}. {tc.function.name}</div>
                <pre className="text-gray-400 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] bg-surface-950 rounded p-2">
                  {typeof args === 'string' ? args : JSON.stringify(args, null, 2)}
                </pre>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
