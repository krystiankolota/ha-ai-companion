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

export function generateSuggestions(resourceTypes, extraPrompt) {
  const body = { resource_types: resourceTypes }
  if (extraPrompt) body.extra_prompt = extraPrompt
  return apiFetch('api/suggestions/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
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

// Logs
export function getLogs(lines = 200, filter = '') {
  const params = new URLSearchParams({ lines })
  if (filter) params.set('filter', filter)
  return apiFetch(`api/logs?${params}`)
}

export function analyzeLogs(lines) {
  return apiFetch('api/logs/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }),
  })
}
