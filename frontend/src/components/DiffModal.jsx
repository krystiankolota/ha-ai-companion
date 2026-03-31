import { useEffect, useRef } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import * as Diff from 'diff'
import { Diff2HtmlUI } from 'diff2html/lib/ui/js/diff2html-ui-base'

function buildUnifiedDiff(filePath, oldContent, newContent) {
  return Diff.createPatch(filePath, oldContent || '', newContent || '', 'Original', 'Proposed')
}

export default function DiffModal() {
  const { state, dispatch } = useAppContext()
  const { activeDiffChangeset } = state
  const diffContainerRef = useRef(null)

  const close = () => dispatch({ type: Actions.SET_ACTIVE_DIFF, payload: null })

  useEffect(() => {
    if (!activeDiffChangeset || !diffContainerRef.current) return

    const { file_changes_detail, original_contents, files } = activeDiffChangeset

    let combinedDiff = ''

    if (file_changes_detail && file_changes_detail.length > 0) {
      for (const change of file_changes_detail) {
        const originalContent = original_contents?.[change.file_path] || ''
        combinedDiff += buildUnifiedDiff(change.file_path, originalContent, change.new_content)
      }
    } else if (files && files.length > 0) {
      // No detail available — just show file list as a placeholder diff
      for (const f of files) {
        combinedDiff += buildUnifiedDiff(f, '', '(new content pending)')
      }
    }

    if (combinedDiff) {
      const diff2htmlUi = new Diff2HtmlUI(diffContainerRef.current, combinedDiff, {
        drawFileList: true,
        matching: 'lines',
        outputFormat: 'side-by-side',
        highlight: false,
        fileContentToggle: true,
      })
      diff2htmlUi.draw()
    }
  }, [activeDiffChangeset])

  if (!activeDiffChangeset) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-surface-900 border border-surface-700 rounded-2xl w-full max-w-5xl max-h-[90vh] flex flex-col shadow-2xl">
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
            className="text-gray-400 hover:text-gray-200 transition-colors text-xl leading-none"
          >
            ✕
          </button>
        </div>

        {/* Diff content */}
        <div className="flex-1 overflow-auto p-4">
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
