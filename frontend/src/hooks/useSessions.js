import { useCallback, useRef } from 'react'
import { useAppContext } from '../store/AppContext'
import { Actions } from '../store/reducer'
import {
  getSessions,
  getSession,
  saveSession as apiSaveSession,
  deleteSession as apiDeleteSession,
} from '../lib/api'
import { generateSessionId } from '../lib/utils'

// Helper: find tool name from history by tool_call_id
function findToolName(messages, toolCallId) {
  for (const m of messages) {
    if (m.role === 'assistant' && m.tool_calls) {
      const tc = m.tool_calls.find(t => t.id === toolCallId)
      if (tc) return tc.function.name
    }
  }
  return 'tool'
}

// Helper: build displayMessages from conversation history (for session replay)
function buildDisplayMessages(history) {
  const msgs = []
  let id = 0
  const nextId = () => String(++id)

  for (const msg of history) {
    if (msg.role === 'user') {
      msgs.push({ id: nextId(), type: 'user', content: msg.content })
    } else if (msg.role === 'assistant') {
      if (msg.content) {
        msgs.push({ id: nextId(), type: 'assistant', content: msg.content, isStreaming: false })
      }
      if (msg.tool_calls) {
        const toolNames = msg.tool_calls.map(tc => tc.function.name)
        msgs.push({ id: nextId(), type: 'tool_call', toolNames, toolCalls: msg.tool_calls })
      }
    } else if (msg.role === 'tool') {
      const toolName = findToolName(history, msg.tool_call_id)
      try {
        const result = JSON.parse(msg.content)
        msgs.push({ id: nextId(), type: 'tool_result', functionName: toolName, result })
        if (toolName === 'propose_config_changes' && result.success) {
          msgs.push({
            id: nextId(),
            type: 'system',
            content: `Config change proposed (${result.total_files} file(s)) — changeset expired`,
          })
        }
      } catch (e) {
        // Malformed tool result — skip rendering
      }
    } else if (msg.role === 'system_info') {
      msgs.push({ id: nextId(), type: 'system', content: msg.content })
    }
  }

  return msgs
}

export function useSessions() {
  const { state, dispatch } = useAppContext()
  const retryRef = useRef(false)

  const loadSessions = useCallback(async (retryOnEmpty = true) => {
    try {
      const data = await getSessions()
      const sessions = data.sessions || data
      dispatch({ type: Actions.SET_SESSIONS, payload: sessions })
      if (retryOnEmpty && (!sessions || sessions.length === 0) && !retryRef.current) {
        retryRef.current = true
        setTimeout(() => {
          retryRef.current = false
          loadSessions(false)
        }, 2000)
      }
    } catch (e) {
      console.error('Failed to load sessions:', e)
    }
  }, [dispatch])

  const saveSession = useCallback(async (sessionId, conversationHistory) => {
    if (!conversationHistory || conversationHistory.length === 0) return
    try {
      const firstUser = conversationHistory.find(m => m.role === 'user')
      const title = firstUser
        ? String(firstUser.content).substring(0, 60).trim()
        : 'New conversation'
      await apiSaveSession(sessionId, title, conversationHistory)
      await loadSessions(false)
    } catch (e) {
      console.error('Auto-save error:', e)
    }
  }, [loadSessions])

  const deleteSession = useCallback(async (sessionId) => {
    if (!window.confirm('Delete this conversation?')) return
    try {
      await apiDeleteSession(sessionId)
      if (sessionId === state.currentSessionId) {
        dispatch({ type: Actions.NEW_CHAT })
      }
      await loadSessions(false)
    } catch (e) {
      console.error('Delete session error:', e)
    }
  }, [state.currentSessionId, dispatch, loadSessions])

  const switchSession = useCallback(async (sessionId, resetWS) => {
    if (sessionId === state.currentSessionId) return
    if (typeof resetWS === 'function') resetWS()

    try {
      const session = await getSession(sessionId)
      const messages = session.messages || []
      const displayMessages = buildDisplayMessages(messages)

      dispatch({
        type: Actions.SELECT_SESSION,
        payload: {
          id: sessionId,
          messages,
          displayMessages: [
            ...displayMessages,
            {
              id: 'loaded-' + sessionId,
              type: 'system',
              content: `Loaded: ${session.title || 'Conversation'}`,
            },
          ],
        },
      })
      dispatch({ type: Actions.CLOSE_SIDEBAR })
      await loadSessions(false)
    } catch (e) {
      console.error('Load session error:', e)
      dispatch({
        type: Actions.ADD_DISPLAY_MESSAGE,
        payload: { type: 'system', content: 'Failed to load conversation.' },
      })
    }
  }, [state.currentSessionId, dispatch, loadSessions])

  const newChat = useCallback(async () => {
    dispatch({ type: Actions.NEW_CHAT })
    await loadSessions(false)
  }, [dispatch, loadSessions])

  return { loadSessions, saveSession, deleteSession, switchSession, newChat }
}
