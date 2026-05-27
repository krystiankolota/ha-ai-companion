import { useEffect, useRef } from 'react'
import { renderMarkdown } from '../../lib/utils'

function attachCopyButtons(container) {
  if (!container) return
  container.querySelectorAll('pre').forEach(pre => {
    if (pre.querySelector('.copy-btn')) return
    pre.style.position = 'relative'

    const btn = document.createElement('button')
    btn.textContent = 'Copy'
    btn.className = 'copy-btn'
    btn.setAttribute('aria-label', 'Copy code')
    Object.assign(btn.style, {
      position: 'absolute',
      top: '6px',
      right: '6px',
      padding: '2px 10px',
      fontSize: '11px',
      lineHeight: '1.6',
      background: '#1f2937',
      color: '#9ca3af',
      border: '1px solid #374151',
      borderRadius: '4px',
      cursor: 'pointer',
      opacity: '0.85',
      zIndex: '1',
    })

    btn.addEventListener('mouseenter', () => { btn.style.opacity = '1'; btn.style.color = '#e5e7eb' })
    btn.addEventListener('mouseleave', () => { btn.style.opacity = '0.85'; btn.style.color = '#9ca3af' })

    btn.addEventListener('click', async () => {
      const code = pre.querySelector('code')
      const text = (code || pre).textContent
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        // Safari / older browsers fallback
        const ta = Object.assign(document.createElement('textarea'), {
          value: text,
          style: 'position:fixed;top:-9999px',
        })
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      }
      btn.textContent = 'Copied!'
      btn.style.color = '#6ee7b7'
      setTimeout(() => { btn.textContent = 'Copy'; btn.style.color = '#9ca3af' }, 2000)
    })

    pre.appendChild(btn)
  })
}

export default function AssistantMessage({ content, isStreaming }) {
  const contentRef = useRef(null)

  useEffect(() => {
    if (!isStreaming) attachCopyButtons(contentRef.current)
  }, [content, isStreaming])

  return (
    <div className="flex justify-start">
      <div className="bg-surface-900 border border-surface-700 px-4 py-3 rounded-2xl rounded-bl-sm max-w-[85%] break-words">
        {isStreaming ? (
          <span className="text-gray-200 whitespace-pre-wrap">
            {content}
            <span className="inline-block w-0.5 h-4 bg-indigo-400 ml-0.5 align-middle animate-pulse" />
          </span>
        ) : (
          <div
            ref={contentRef}
            className="prose"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
          />
        )}
      </div>
    </div>
  )
}
