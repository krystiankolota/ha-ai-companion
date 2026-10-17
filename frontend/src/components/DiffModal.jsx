import { useEffect, useRef } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import { Diff2HtmlUI } from 'diff2html/lib/ui/js/diff2html-ui-base'

const ITEM_GROUPS = [
  ['added', 'Added', 'text-green-400'],
  ['changed', 'Changed', 'text-amber-300'],
  ['removed', 'Removed', 'text-red-400'],
]

export default function DiffModal() {
  const { state, dispatch } = useAppContext()
  const { activeDiffChangeset } = state
  const diffContainerRef = useRef(null)

  const close = () => dispatch({ type: Actions.SET_ACTIVE_DIFF, payload: null })
  const changes = activeDiffChangeset?.file_changes_detail || []

  useEffect(() => {
    if (!activeDiffChangeset || !diffContainerRef.current) return
    diffContainerRef.current.innerHTML = ''
    // Diffs are computed server-side from the exact content the proposal was
    // built on — the UI only renders them.
    const combinedDiff = changes.map(c => c.diff || '').join('')
    if (combinedDiff) {
      const diff2htmlUi = new Diff2HtmlUI(diffContainerRef.current, combinedDiff, {
        drawFileList: true,
        matching: 'lines',
        outputFormat: 'line-by-line',
        highlight: false,
        fileContentToggle: true,
      })
      diff2htmlUi.draw()
    }
  }, [activeDiffChangeset]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!activeDiffChangeset) return null

  const loaded = changes.length > 0
  const noTextChange = loaded && changes.every(c => !c.diff)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-2 sm:p-4">
      <div className="bg-surface-900 border border-surface-700 rounded-2xl w-full max-w-5xl max-h-[95vh] sm:max-h-[90vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface-700 flex-shrink-0">
          <div>
            <h2 className="text-base font-semibold text-gray-100">Proposed Changes</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              {activeDiffChangeset.total_files} file(s) · ID: {activeDiffChangeset.changeset_id}
            </p>
          </div>
          <button
            onClick={close}
            aria-label="Close"
            className="text-gray-400 hover:text-gray-200 transition-colors text-xl leading-none p-2 -m-2"
          >
            ✕
          </button>
        </div>

        {/* Diff content */}
        <div className="flex-1 overflow-auto p-4">
          {changes.filter(c => c.items).map(c => (
            <div key={c.file_path} className="mb-3 text-sm">
              <div className="text-gray-400 text-xs mb-1">{c.file_path}</div>
              {ITEM_GROUPS.map(([key, label, color]) => (c.items[key] || []).length > 0 && (
                <div key={key} className={color}>
                  {label}: {c.items[key].join(', ')}
                </div>
              ))}
            </div>
          ))}
          {!loaded && <p className="text-sm text-gray-400">Diff not available — the changeset was applied, rejected, or has expired.</p>}
          {noTextChange && <p className="text-sm text-gray-400">No text changes.</p>}
          <div ref={diffContainerRef} className="text-sm" />
        </div>

        {/* Footer */}
        <div className="flex justify-end px-5 py-4 border-t border-surface-700 flex-shrink-0">
          <button
            onClick={close}
            className="px-4 py-2 bg-surface-800 hover:bg-surface-700 border border-surface-600 text-gray-300 rounded-lg text-sm transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
