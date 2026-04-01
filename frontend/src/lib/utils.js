import { marked } from 'marked'
import DOMPurify from 'dompurify'

export function generateSessionId() {
  return 'sess-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9)
}

export function formatSessionDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const diff = Date.now() - d.getTime()
    if (diff < 60000) return 'just now'
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago'
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago'
    return d.toLocaleDateString()
  } catch (e) {
    return ''
  }
}

export function escapeHtml(str) {
  if (!str) return ''
  const div = document.createElement('div')
  div.textContent = String(str)
  return div.innerHTML
}

// Configure marked for safe rendering
marked.setOptions({
  breaks: true,
  gfm: true,
})

export function renderMarkdown(content) {
  if (!content) return ''
  try {
    const raw = marked.parse(content)
    return DOMPurify.sanitize(raw)
  } catch (e) {
    return escapeHtml(content)
  }
}

export function formatGeneratedAt(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
}

export function generateId() {
  return Date.now() + '-' + Math.random().toString(36).substr(2, 9)
}
