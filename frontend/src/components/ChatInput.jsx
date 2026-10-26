import { useRef, useCallback, useEffect, useState } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import { MAX_IMAGES, MAX_TOTAL_CHARS, shrinkImage } from '../lib/images'

export default function ChatInput({ onSend, onStop }) {
  const { state, dispatch } = useAppContext()
  const { isSending, chatPrefill } = state
  const textareaRef = useRef(null)
  const fileRef = useRef(null)
  const [images, setImages] = useState([])
  const [imageError, setImageError] = useState('')
  const [pending, setPending] = useState(0)
  const genRef = useRef(0) // bumped on send so late conversions cannot attach to the next message

  const addFiles = useCallback(async (files) => {
    const picked = Array.from(files || []).filter(f => f.type.startsWith('image/'))
    if (!picked.length) return
    setImageError('')
    const gen = genRef.current
    setPending(n => n + picked.length)
    for (const f of picked) {
      let url = null
      try {
        url = await shrinkImage(f)
      } catch {
        setImageError(`Could not read ${f.name || 'image'}.`)
      }
      setPending(n => n - 1)
      if (!url || gen !== genRef.current) continue
      setImages(prev => {
        if (prev.length >= MAX_IMAGES) { setImageError(`At most ${MAX_IMAGES} images.`); return prev }
        if (prev.reduce((n, u) => n + u.length, url.length) > MAX_TOTAL_CHARS) { setImageError('Images are too large.'); return prev }
        return [...prev, url]
      })
    }
  }, [])

  const handlePaste = useCallback((e) => {
    const files = Array.from(e.clipboardData?.items || [])
      .filter(i => i.kind === 'file' && i.type.startsWith('image/'))
      .map(i => i.getAsFile())
    if (files.length) {
      e.preventDefault()
      addFiles(files)
    }
  }, [addFiles])

  const handleDrop = useCallback((e) => {
    if (!e.dataTransfer?.files?.length) return
    e.preventDefault()
    addFiles(e.dataTransfer.files)
  }, [addFiles])

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
    if ((!text && !images.length) || isSending || pending > 0) return
    el.value = ''
    el.style.height = 'auto'
    onSend(text || 'Look at the attached image(s).', images)
    genRef.current++
    setImages([])
    setImageError('')
  }, [isSending, onSend, images, pending])

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  return (
    <div className="bg-surface-900 border-t border-surface-700 p-4" onDragOver={e => e.preventDefault()} onDrop={handleDrop}>
      {(images.length > 0 || imageError || pending > 0) && (
        <div className="flex flex-wrap gap-2 items-center max-w-4xl mx-auto mb-2">
          {images.map((url, i) => (
            <div key={i} className="relative">
              <img src={url} alt={`Attachment ${i + 1}`} className="h-16 w-16 object-cover rounded-lg border border-surface-700" />
              <button
                onClick={() => setImages(prev => prev.filter((_, j) => j !== i))}
                aria-label={`Remove attachment ${i + 1}`}
                className="absolute -top-2 -right-2 bg-surface-700 hover:bg-red-700 text-white rounded-full w-5 h-5 text-xs leading-5"
              >×</button>
            </div>
          ))}
          {pending > 0 && <span className="text-xs text-gray-400">Preparing image…</span>}
          {imageError && <span className="text-xs text-amber-400">{imageError}</span>}
        </div>
      )}
      <div className="flex gap-3 items-end max-w-4xl mx-auto">
        <input ref={fileRef} type="file" accept="image/*" multiple className="hidden"
          onChange={e => { addFiles(e.target.files); e.target.value = '' }} />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={isSending}
          title="Attach images"
          aria-label="Attach images"
          className="flex-shrink-0 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 border border-surface-700 text-gray-300 rounded-xl px-3 py-3 text-sm transition-colors"
        >📎</button>
        <textarea
          ref={textareaRef}
          className="flex-1 resize-none bg-surface-800 border border-surface-700 text-gray-100 placeholder-gray-500 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500 transition-colors min-h-[48px] max-h-[200px]"
          placeholder={isSending ? 'AI is working… (Stop to interrupt)' : 'Ask about your Home Assistant…'}
          rows={1}
          disabled={isSending}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
        />
        {isSending ? (
          <button
            onClick={onStop}
            title="Stop the AI"
            className="flex-shrink-0 bg-red-700 hover:bg-red-600 text-white rounded-xl px-5 py-3 text-sm font-medium transition-colors"
          >
            Stop
          </button>
        ) : (
          <button
            onClick={handleSend}
            className="flex-shrink-0 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl px-5 py-3 text-sm font-medium transition-colors"
          >
            Send
          </button>
        )}
      </div>
    </div>
  )
}
