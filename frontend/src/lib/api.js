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

// Usage
export function getUsage(days = 30) {
  return apiFetch(`api/usage?days=${days}`)
}

export function getModelAdvice(refresh = false) {
  return apiFetch(`api/models/advice${refresh ? '?refresh=true' : ''}`)
}

// Approval
export function submitApproval(changeId, approved) {
  return apiFetch('api/approve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ change_id: changeId, approved, validate: true }),
  })
}

export function getChangeset(changesetId) {
  return apiFetch(`api/changeset/${changesetId}`)
}

export function getAppliedChangesets(limit = 20) {
  return apiFetch(`api/changesets?limit=${limit}`)
}

export function restoreChangeset(changesetId) {
  return apiFetch(`api/changesets/${changesetId}/restore`, { method: 'POST' })
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
  const handle = (line) => {
    if (!line.trim()) return
    let event
    try { event = JSON.parse(line) } catch { return }
    if (event.event === 'status' && onStatus) onStatus(event.message)
    else if (event.event === 'context_ready' && onContextReady) onContextReady(event)
    else if (event.event === 'result') result = event
    else if (event.event === 'error') throw new Error(event.message || 'Suggestion generation failed')
  }
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()
    lines.forEach(handle)
  }
  handle(buffer + decoder.decode())
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
export function getMemories() {
  return apiFetch('api/memory')
}

export function createMemory(kind, text) {
  return apiFetch('api/memory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, text }),
  })
}

export function updateMemory(id, fields) {
  return apiFetch(`api/memory/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  })
}

export function deleteMemory(id) {
  return apiFetch(`api/memory/${id}`, { method: 'DELETE' })
}

export function runMemoryDoctor() {
  return apiFetch('api/memory/doctor', { method: 'POST' })
}
