import { useRef, useCallback, useEffect } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'

export default function ChatInput({ onSend }) {
  const { state, dispatch } = useAppContext()
  const { isSending, chatPrefill } = state
  const textareaRef = useRef(null)

  useEffect(() => {
    if (!chatPrefill) return
    const el = textareaRef.current
    if (!el) return
    el.value = chatPrefill
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    el.focus()
    dispatch({ type: Actions.SET_CHAT_PREFILL, payload: null })
  }, [chatPrefill, dispatch])

  const handleInput = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [])

  const handleSend = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    const text = el.value.trim()
    if (!text || isSending) return
    el.value = ''
    el.style.height = 'auto'
    onSend(text)
  }, [isSending, onSend])

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  return (
    <div className="bg-surface-900 border-t border-surface-700 p-4">
      <div className="flex gap-3 items-end max-w-4xl mx-auto">
        <textarea
          ref={textareaRef}
          className="flex-1 resize-none bg-surface-800 border border-surface-700 text-gray-100 placeholder-gray-500 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500 transition-colors min-h-[48px] max-h-[200px]"
          placeholder={isSending ? 'AI is thinking…' : 'Ask about your Home Assistant…'}
          rows={1}
          disabled={isSending}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
        />
        <button
          onClick={handleSend}
          disabled={isSending}
          className="flex-shrink-0 bg-indigo-600 hover:bg-indigo-500 disabled:bg-surface-700 disabled:text-gray-500 text-white rounded-xl px-5 py-3 text-sm font-medium transition-colors"
        >
          {isSending ? (
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </span>
          ) : (
            'Send'
          )}
        </button>
      </div>
    </div>
  )
}
