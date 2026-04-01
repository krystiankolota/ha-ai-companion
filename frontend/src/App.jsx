import { useCallback, useEffect, useRef } from 'react'
import { AppProvider } from './store/AppContext'
import { useAppContext } from './store/AppContext'
import { Actions } from './store/reducer'
import { useSessions } from './hooks/useSessions'
import { useWebSocket } from './hooks/useWebSocket'

import Sidebar from './components/Sidebar'
import Header from './components/Header'
import ChatTab from './components/ChatTab'
import SuggestionsTab from './components/SuggestionsTab'
import LogsTab from './components/LogsTab'
import Footer from './components/Footer'
import DiffModal from './components/DiffModal'
import ClearAllModal from './components/ClearAllModal'

// Debounce helper
function useDebounce(fn, delay) {
  const timerRef = useRef(null)
  const fnRef = useRef(fn)
  fnRef.current = fn
  return useCallback((...args) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => fnRef.current(...args), delay)
  }, [delay])
}

// Inner app — all hooks here have access to AppContext
function AppInner() {
  const { state, dispatch } = useAppContext()
  const stateRef = useRef(state)
  stateRef.current = state

  // Stable getter for conversation history (avoids stale closure in WS hook)
  const getConversationHistory = useCallback(() => stateRef.current.conversationHistory, [])

  // Auto-save implementation
  const autoSaveFn = useCallback(async () => {
    const s = stateRef.current
    if (!s.conversationHistory || s.conversationHistory.length === 0) return
    const firstUser = s.conversationHistory.find(m => m.role === 'user')
    const title = firstUser ? String(firstUser.content).substring(0, 60).trim() : 'New conversation'
    try {
      await fetch(`api/sessions/${s.currentSessionId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, messages: s.conversationHistory }),
      })
    } catch (e) {
      console.error('Auto-save error:', e)
    }
  }, [])

  const debouncedAutoSave = useDebounce(autoSaveFn, 1000)

  const { loadSessions, deleteSession, switchSession, newChat } = useSessions()

  const { sendMessage, resetWS, connect } = useWebSocket(
    dispatch,
    getConversationHistory,
    debouncedAutoSave,
  )

  // Initial setup
  useEffect(() => {
    loadSessions()
    connect()

    fetch('health')
      .then(r => r.json())
      .then(data => {
        const content = data.agent_system_ready
          ? '✅ HA AI Companion ready. How can I help you today?'
          : '⚠️ AI system not ready. Please configure OPENAI_API_KEY.'
        dispatch({ type: Actions.ADD_DISPLAY_MESSAGE, payload: { type: 'system', content } })
      })
      .catch(() => {
        dispatch({
          type: Actions.ADD_DISPLAY_MESSAGE,
          payload: { type: 'system', content: '❌ Failed to connect to agent system.' },
        })
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Reload sessions on visibility change (fixes mobile HA app reload issue)
  useEffect(() => {
    const handler = () => { if (!document.hidden) loadSessions() }
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, [loadSessions])

  // Save on beforeunload
  useEffect(() => {
    const handler = () => {
      const s = stateRef.current
      if (!s.conversationHistory || s.conversationHistory.length === 0) return
      const firstUser = s.conversationHistory.find(m => m.role === 'user')
      const title = firstUser ? String(firstUser.content).substring(0, 60).trim() : 'New conversation'
      const payload = JSON.stringify({ title, messages: s.conversationHistory })
      navigator.sendBeacon(
        `api/sessions/${s.currentSessionId}`,
        new Blob([payload], { type: 'application/json' })
      )
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  // Handle chat prefill from suggestions tab
  useEffect(() => {
    const handler = (e) => {
      const { message } = e.detail || {}
      if (!message) return
      const textarea = document.querySelector('main textarea')
      if (textarea) {
        textarea.value = message
        textarea.dispatchEvent(new Event('input'))
        textarea.focus()
      }
    }
    window.addEventListener('ha-chat-prefill', handler)
    return () => window.removeEventListener('ha-chat-prefill', handler)
  }, [])

  const handleSwitchSession = useCallback((sessionId) => {
    switchSession(sessionId, resetWS)
  }, [switchSession, resetWS])

  const handleDeleteSession = useCallback((sessionId) => {
    deleteSession(sessionId)
  }, [deleteSession])

  const handleClearAll = useCallback(() => {
    dispatch({ type: Actions.SHOW_CLEAR_ALL_MODAL })
  }, [dispatch])

  const handleClearAllDone = useCallback(async () => {
    dispatch({ type: Actions.NEW_CHAT })
    await loadSessions(false)
  }, [dispatch, loadSessions])

  const closeSidebar = useCallback(() => {
    dispatch({ type: Actions.CLOSE_SIDEBAR })
  }, [dispatch])

  return (
    <div className="flex h-full overflow-hidden bg-surface-950">
      <Sidebar
        onNewChat={newChat}
        onSwitchSession={handleSwitchSession}
        onDeleteSession={handleDeleteSession}
        onClearAll={handleClearAll}
      />

      {/* Mobile backdrop */}
      {state.sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 sm:hidden"
          onClick={closeSidebar}
        />
      )}

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0">
        <Header />
        <main className="flex-1 overflow-hidden">
          {state.activeTab === 'chat' && <ChatTab onSend={sendMessage} />}
          {state.activeTab === 'suggestions' && <SuggestionsTab />}
          {state.activeTab === 'logs' && <LogsTab />}
        </main>
        <Footer />
      </div>

      <DiffModal />
      <ClearAllModal onDone={handleClearAllDone} />
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <AppInner />
    </AppProvider>
  )
}
