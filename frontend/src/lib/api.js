// Thin fetch wrappers for all backend endpoints

async function apiFetch(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    let errorDetail
    try {
      const err = await response.json()
      errorDetail = err.detail || err.error || response.statusText
    } catch {
      errorDetail = response.statusText
    }
    throw new Error(errorDetail)
  }
  return response.json()
}

// Health
export function checkHealth() {
  return apiFetch('health')
}

// Sessions
export function getSessions() {
  return apiFetch('api/sessions')
}

export function getSession(sessionId) {
  return apiFetch(`api/sessions/${sessionId}`)
}

export function saveSession(sessionId, title, messages) {
  return apiFetch(`api/sessions/${sessionId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, messages }),
  })
}

export function deleteSession(sessionId) {
  return apiFetch(`api/sessions/${sessionId}`, { method: 'DELETE' })
}

export function clearAllSessions() {
  return apiFetch('api/sessions/clear-all', { method: 'POST' })
}

// Approval
export function submitApproval(changeId, approved) {
  return apiFetch('api/approve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ change_id: changeId, approved, validate: true }),
  })
}

// Suggestions
export function getSuggestions() {
  return apiFetch('api/suggestions')
}

export async function generateSuggestions(resourceTypes, extraPrompt, onStatus, onContextReady) {
  const body = { resource_types: resourceTypes }
  if (extraPrompt) body.extra_prompt = extraPrompt
  const response = await fetch('api/suggestions/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    let detail
    try { detail = (await response.json()).detail } catch { detail = response.statusText }
    throw new Error(detail || response.statusText)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result = null
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const event = JSON.parse(line)
        if (event.event === 'status' && onStatus) {
          onStatus(event.message)
        } else if (event.event === 'context_ready' && onContextReady) {
          onContextReady(event)
        } else if (event.event === 'result') {
          result = event
        } else if (event.event === 'error') {
          throw new Error(event.message)
        }
      } catch (e) {
        if (e.message && !line.includes(e.message)) throw e
      }
    }
  }
  return result
}

export function dismissSuggestion(title) {
  return apiFetch('api/suggestions/dismiss', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export function getDismissedSuggestions() {
  return apiFetch('api/suggestions/dismissed')
}

export function clearDismissedSuggestions() {
  return apiFetch('api/suggestions/dismissed', { method: 'DELETE' })
}

export function restoreDismissedSuggestion(title) {
  return apiFetch('api/suggestions/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export function getAppliedSuggestions() {
  return apiFetch('api/suggestions/applied')
}

export function markSuggestionApplied(title) {
  return apiFetch('api/suggestions/applied', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export function getSuggestionsHistory() {
  return apiFetch('api/suggestions/history')
}

// Memory
export function getMemoryFiles() {
  return apiFetch('api/memory')
}

export function getMemoryFile(filename) {
  return apiFetch(`api/memory/${encodeURIComponent(filename)}`)
}

export function deleteMemoryFile(filename) {
  return apiFetch(`api/memory/${encodeURIComponent(filename)}`, { method: 'DELETE' })
}

export function updateMemoryFile(filename, content) {
  return apiFetch(`api/memory/${encodeURIComponent(filename)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export function createMemoryFile(filename, content) {
  return apiFetch('api/memory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, content }),
  })
}
