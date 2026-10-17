import { useRef, useCallback, useState } from 'react'
import { Actions } from '../store/reducer'
import { generateId } from '../lib/utils'
import { changesetFromResult } from '../lib/changesets'
import { toolLabel, makeArgsSummary, makeResultSummary } from '../lib/toolLabels'

// Per-session markers for runs that may still be executing server-side.
// Survives the HA panel iframe being destroyed (tab switch) and session
// switches, so returning to a session resumes from the last processed event.
const RUN_MARKERS_KEY = 'ha_ai_run_markers'
const ACTIVE_RUN_MAX_AGE_MS = 15 * 60 * 1000

function readMarkers() {
  try {
    const all = JSON.parse(localStorage.getItem(RUN_MARKERS_KEY) || '{}')
    const now = Date.now()
    for (const [sid, m] of Object.entries(all)) {
      if (!m || now - (m.ts || 0) > ACTIVE_RUN_MAX_AGE_MS) delete all[sid]
    }
    return all
  } catch {
    return {}
  }
}

function readRunMarker(sessionId) {
  return sessionId ? readMarkers()[sessionId] || null : null
}

function writeRunMarker(sessionId, lastSeq) {
  try {
    const all = readMarkers()
    all[sessionId] = { lastSeq, ts: Date.now() }
    localStorage.setItem(RUN_MARKERS_KEY, JSON.stringify(all))
  } catch { /* storage full/blocked — resume degrades gracefully */ }
}

function clearRunMarker(sessionId) {
  if (!sessionId) return
  try {
    const all = readMarkers()
    delete all[sessionId]
    localStorage.setItem(RUN_MARKERS_KEY, JSON.stringify(all))
  } catch {}
}

export function useWebSocket(dispatch, getConversationHistory, onAutoSave, getSessionId) {
  const wsRef = useRef(null)
  const loadingIndicatorIdRef = useRef(null)
  const toolCallArgumentsRef = useRef({})
  const streamingIdRef = useRef(null)
  const breadcrumbsMsgIdRef = useRef(null)  // ID of the active breadcrumbs message for this turn
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef(null)
  const connectRef = useRef(null) // forward ref to break circular dependency
  const handleMessageRef = useRef(null) // forward ref to break circular dependency
  const lastSeqRef = useRef(-1)          // highest event seq seen for the active run
  const activeRunRef = useRef(null)      // session_id of the in-flight run, or null
  const resumeGenRef = useRef(0)         // bumped to invalidate an in-flight resumeSession()
  const [isConnected, setIsConnected] = useState(false)

  const addLoadingIndicator = useCallback((status = '') => {
    const id = generateId()
    loadingIndicatorIdRef.current = id
    dispatch({ type: Actions.ADD_DISPLAY_MESSAGE, payload: { type: 'loading', id } })
    if (status) {
      dispatch({ type: Actions.SET_LOADING, payload: status })
    }
    return id
  }, [dispatch])

  const removeLoadingIndicator = useCallback(() => {
    loadingIndicatorIdRef.current = null
    dispatch({ type: Actions.SET_LOADING, payload: null })
  }, [dispatch])

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current) return
    const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000)
    reconnectAttemptsRef.current++
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null
      if (!wsRef.current || wsRef.current.readyState === WebSocket.CLOSED) {
        console.log(`Reconnecting (attempt ${reconnectAttemptsRef.current})…`)
        if (connectRef.current) connectRef.current()
      }
    }, delay)
  }, [])

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return wsRef.current
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}${window.location.pathname}ws/chat`

    console.log('Connecting to WebSocket:', wsUrl)
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      console.log('WebSocket connected')
      reconnectAttemptsRef.current = 0
      setIsConnected(true)

      // A run was in flight when the connection dropped — resume it
      if (activeRunRef.current) {
        console.log(`Resuming run for session ${activeRunRef.current} from seq ${lastSeqRef.current}`)
        try {
          ws.send(JSON.stringify({
            type: 'resume',
            session_id: activeRunRef.current,
            last_seq: lastSeqRef.current,
          }))
        } catch (e) {
          console.error('Resume send failed:', e)
        }
      }
    }

    ws.onerror = (error) => {
      console.error('WebSocket error:', error)
      dispatch({
        type: Actions.ADD_DISPLAY_MESSAGE,
        payload: { type: 'system', content: '❌ WebSocket connection error' },
      })
    }

    ws.onclose = () => {
      console.log('WebSocket closed')
      wsRef.current = null
      setIsConnected(false)

      if (activeRunRef.current) {
        // Run keeps executing server-side — keep the UI in "working" state
        // and let the reconnect + resume flow pick the stream back up.
        dispatch({ type: Actions.SET_LOADING, payload: 'Connection lost — AI still working, reconnecting…' })
      } else {
        // If we were mid-response, clean up so the UI doesn't freeze
        if (loadingIndicatorIdRef.current) {
          removeLoadingIndicator()
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: {
              type: 'system',
              content: '⚠️ Connection lost. Your message may not have been processed — please try again.',
            },
          })
        }
        dispatch({ type: Actions.SET_SENDING, payload: false })
      }

      if (typeof onAutoSave === 'function') {
        onAutoSave()
      }

      scheduleReconnect()
    }

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data)
        if (handleMessageRef.current) handleMessageRef.current(message)
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e)
      }
    }

    return ws
  }, [dispatch, removeLoadingIndicator, onAutoSave, scheduleReconnect])

  const handleMessage = useCallback((message) => {
    const eventType = message.event
    const data = message.data

    console.log('WebSocket event:', eventType)

    // Sequence tracking: drop duplicates from replay overlap, advance the
    // resume cursor, and persist the marker (skipped for token events — too
    // chatty for localStorage; message_complete carries the full text anyway).
    if (typeof message.seq === 'number') {
      if (message.seq <= lastSeqRef.current) return
      lastSeqRef.current = message.seq
      if (activeRunRef.current && eventType !== 'token') {
        writeRunMarker(activeRunRef.current, message.seq)
      }
    }

    try {
      if (eventType === 'resumed') {
        console.log('Run resumed:', data)
        // The replay may already have delivered `complete` (pump runs before
        // this ack) — only show "working" if the run is still active.
        if (activeRunRef.current && !loadingIndicatorIdRef.current) addLoadingIndicator('Resumed — AI is working…')
        return
      }
      if (eventType === 'resume_failed') {
        console.warn('Resume failed:', data)
        clearRunMarker(activeRunRef.current)
        activeRunRef.current = null
        lastSeqRef.current = -1
        removeLoadingIndicator()
        dispatch({ type: Actions.SET_SENDING, payload: false })
        dispatch({
          type: Actions.ADD_DISPLAY_MESSAGE,
          payload: {
            type: 'system',
            content: '⚠️ The AI finished while you were away. Reopen this conversation from the sidebar to see the result.',
          },
        })
        return
      }
      if (eventType === 'token') {
        // Remove loading indicator on first token
        if (loadingIndicatorIdRef.current) {
          removeLoadingIndicator()
        }

        // Create streaming message if not yet created
        if (!streamingIdRef.current) {
          const id = generateId()
          streamingIdRef.current = id
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: { type: 'assistant', content: '', isStreaming: true, id },
          })
        }

        dispatch({ type: Actions.UPDATE_STREAMING, payload: data.content })

      } else if (eventType === 'message_complete') {
        // Push completed message to history
        dispatch({ type: Actions.PUSH_TO_HISTORY, payload: data.message })

        // Finalize streaming message
        if (streamingIdRef.current) {
          dispatch({ type: Actions.COMPLETE_STREAMING })
          // Then replace with the authoritative full text — heals token gaps after resume
          // (COMPLETE_STREAMING writes accumulated tokens, which may be partial)
          if (data.message?.content) {
            dispatch({
              type: Actions.UPDATE_DISPLAY_MESSAGE,
              payload: { id: streamingIdRef.current, updates: { content: data.message.content } },
            })
          }
          streamingIdRef.current = null
        } else if (data.message?.content) {
          // Resumed past all token events — render the message directly
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: { type: 'assistant', content: data.message.content, isStreaming: false },
          })
        }

        // If still sending (more tool calls coming), add loading indicator
        // We check isSending by always adding it here — the complete event will remove it
        if (!loadingIndicatorIdRef.current) {
          addLoadingIndicator('Preparing…')
        }

      } else if (eventType === 'tool_call') {
        // Finalize current streaming message if any
        if (streamingIdRef.current) {
          dispatch({ type: Actions.COMPLETE_STREAMING })
          streamingIdRef.current = null
        }

        // Add assistant+tool_calls to history
        dispatch({
          type: Actions.PUSH_TO_HISTORY,
          payload: {
            role: 'assistant',
            content: '',
            tool_calls: data.tool_calls,
          },
        })

        // Create or extend breadcrumbs message for this turn
        const steps = (data.tool_calls || []).map(tc => ({
          tool_call_id: tc.id,
          name: tc.function?.name || tc.function,
          label: toolLabel(tc.function?.name || tc.function),
          status: 'pending',
          argsSummary: '',
          resultSummary: '',
          args: null,
          result: null,
        }))

        if (!breadcrumbsMsgIdRef.current) {
          // First tool_call this turn — create the breadcrumbs message
          const bcId = generateId()
          breadcrumbsMsgIdRef.current = bcId
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: { id: bcId, type: 'breadcrumbs', steps },
          })
        } else {
          // Subsequent tool_call this turn — append steps to existing breadcrumbs
          dispatch({
            type: Actions.APPEND_BREADCRUMB_STEPS,
            payload: { id: breadcrumbsMsgIdRef.current, steps },
          })
        }

        // Ensure loading indicator present
        const toolNames = (data.tool_calls || []).map(tc => tc.function?.name || tc.function).join(', ')
        if (!loadingIndicatorIdRef.current) {
          addLoadingIndicator(`Running: ${toolNames}…`)
        } else {
          dispatch({ type: Actions.SET_LOADING, payload: `Running: ${toolNames}…` })
        }

      } else if (eventType === 'tool_start') {
        // Store arguments for later use in tool_result
        if (data.tool_call_id && data.arguments) {
          toolCallArgumentsRef.current[data.tool_call_id] = data.arguments
        }

        // Update breadcrumb step to running + add args summary
        if (breadcrumbsMsgIdRef.current) {
          let parsedArgs = data.arguments
          try { if (typeof parsedArgs === 'string') parsedArgs = JSON.parse(parsedArgs) } catch {}
          dispatch({
            type: Actions.UPDATE_BREADCRUMBS,
            payload: {
              id: breadcrumbsMsgIdRef.current,
              tool_call_id: data.tool_call_id,
              updates: {
                status: 'running',
                argsSummary: makeArgsSummary(data.function, parsedArgs),
                args: parsedArgs,
              },
            },
          })
        }

        dispatch({ type: Actions.SET_LOADING, payload: `${toolLabel(data.function)}…` })

      } else if (eventType === 'model') {
        const shortModel = (data.model || '').split('/').pop()
        const label = `Thinking · ${shortModel}${data.iteration > 1 ? ` · step ${data.iteration}` : ''}…`
        if (!loadingIndicatorIdRef.current) addLoadingIndicator(label)
        else dispatch({ type: Actions.SET_LOADING, payload: label })

      } else if (eventType === 'tool_status') {
        dispatch({ type: Actions.SET_LOADING, payload: data.message })

      } else if (eventType === 'tool_result') {
        console.log('Tool result received:', data.function, 'success:', data.result?.success)

        // Add tool result to conversation history
        dispatch({
          type: Actions.PUSH_TO_HISTORY,
          payload: {
            role: 'tool',
            tool_call_id: data.tool_call_id,
            content: JSON.stringify(data.result),
          },
        })

        // Update breadcrumb step to done/error with result summary
        if (breadcrumbsMsgIdRef.current) {
          dispatch({
            type: Actions.UPDATE_BREADCRUMBS,
            payload: {
              id: breadcrumbsMsgIdRef.current,
              tool_call_id: data.tool_call_id,
              updates: {
                status: data.result?.success === false ? 'error' : 'done',
                resultSummary: makeResultSummary(data.function, data.result),
                result: data.result,
              },
            },
          })
        }

        // Handle all changeset-returning tools — show approval card.
        // ApprovalCard fetches the server-side diff itself.
        const changesetData = changesetFromResult(data.function, data.result)
        if (changesetData) {
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: { type: 'approval', changeset: changesetData },
          })
        }

        // Save notification for save_memory
        if (data.function === 'save_memory' && data.result.success && !data.result.duplicate) {
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: {
              type: 'save_notification',
              filename: data.result.id ? `#${data.result.id}` : '',
            },
          })
        }

        // Re-add loading indicator while AI processes tool results
        if (!loadingIndicatorIdRef.current) {
          addLoadingIndicator('AI is thinking…')
        }

        if (typeof onAutoSave === 'function') {
          onAutoSave()
        }

      } else if (eventType === 'complete') {
        console.log('Stream complete:', data)

        if (data.usage) {
          dispatch({
            type: Actions.UPDATE_TOKEN_STATS,
            payload: {
              input: data.usage.input_tokens || 0,
              output: data.usage.output_tokens || 0,
              cached: data.usage.cached_tokens || 0,
              costUsd: data.usage.cost_usd || 0,
            },
          })
        }

        if (data.truncated) {
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: { type: 'system', content: '⚠️ Response was cut short by the provider. If the AI was about to make changes, ask it to continue.' },
          })
        }

        removeLoadingIndicator()
        breadcrumbsMsgIdRef.current = null  // turn is over — next tool_call starts fresh
        clearRunMarker(activeRunRef.current)
        activeRunRef.current = null
        lastSeqRef.current = -1
        dispatch({ type: Actions.SET_SENDING, payload: false })

        if (typeof onAutoSave === 'function') {
          onAutoSave()
        }

      } else if (eventType === 'heartbeat') {
        if (loadingIndicatorIdRef.current) {
          const shortModel = (data.model || '').split('/').pop()
          const secs = `${Math.round(data.elapsed || 0)}s`
          const label = data.phase === 'tool'
            ? `${toolLabel(data.tool)} · ${secs}`
            : data.phase === 'waiting_model'
              ? `Waiting for ${shortModel} · ${secs}`
              : `${shortModel} is thinking · ${secs}`
          dispatch({ type: Actions.SET_LOADING, payload: label })
        }

      } else if (eventType === 'error') {
        if (data.code === 'run_active') {
          // This session already has a run (another tab/device, or an earlier
          // message). Attach to it live instead of leaving the UI stuck.
          const sid = typeof getSessionId === 'function' ? getSessionId() : null
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: { type: 'system', content: '⏳ The AI is still working on an earlier message here. Showing its progress — press Stop to interrupt.' },
          })
          if (sid) {
            const gen = resumeGenRef.current
            fetch(`api/runs/${sid}`).then(r => r.json()).then(st => {
              if (gen !== resumeGenRef.current || getSessionId() !== sid) return
              if (st.status !== 'running') {
                removeLoadingIndicator()
                dispatch({ type: Actions.SET_SENDING, payload: false })
                return
              }
              activeRunRef.current = sid
              lastSeqRef.current = st.last_seq
              writeRunMarker(sid, st.last_seq)
              wsRef.current?.send(JSON.stringify({ type: 'resume', session_id: sid, last_seq: st.last_seq }))
            }).catch(() => {
              removeLoadingIndicator()
              dispatch({ type: Actions.SET_SENDING, payload: false })
            })
          }
          return
        }
        if (streamingIdRef.current) {
          dispatch({ type: Actions.COMPLETE_STREAMING })
          streamingIdRef.current = null
        }
        dispatch({
          type: Actions.ADD_DISPLAY_MESSAGE,
          payload: {
            type: 'system',
            content: data.code === 'stopped' ? '⏹ Stopped.' : `❌ Error: ${data.error}`,
          },
        })
        removeLoadingIndicator()
        breadcrumbsMsgIdRef.current = null
        clearRunMarker(activeRunRef.current)
        activeRunRef.current = null
        lastSeqRef.current = -1
        dispatch({ type: Actions.SET_SENDING, payload: false })
        if (typeof onAutoSave === 'function') {
          onAutoSave()
        }
      }
    } catch (e) {
      console.error('Error handling WebSocket message:', e)
    }
  }, [dispatch, removeLoadingIndicator, addLoadingIndicator, getConversationHistory, onAutoSave, getSessionId])

  const sendMessage = useCallback(async (text) => {
    if (!text || !text.trim()) return
    resumeGenRef.current++

    // Add user message to display
    dispatch({
      type: Actions.ADD_DISPLAY_MESSAGE,
      payload: { type: 'user', content: text },
    })

    // Add to conversation history
    dispatch({
      type: Actions.PUSH_TO_HISTORY,
      payload: { role: 'user', content: text },
    })

    // Show loading indicator
    dispatch({ type: Actions.SET_SENDING, payload: true })
    streamingIdRef.current = null
    toolCallArgumentsRef.current = {}
    addLoadingIndicator()

    try {
      const ws = connect()

      // Wait for connection if not open
      if (ws.readyState !== WebSocket.OPEN) {
        await new Promise((resolve, reject) => {
          const prevOnOpen = ws.onopen
          const prevOnError = ws.onerror
          ws.onopen = (e) => {
            if (prevOnOpen) prevOnOpen(e)
            resolve()
          }
          ws.onerror = (e) => {
            if (prevOnError) prevOnError(e)
            reject(new Error('WebSocket connection failed'))
          }
          setTimeout(() => reject(new Error('Connection timeout')), 5000)
        })
      }

      // Send — filter system_info messages (display-only, never sent to LLM)
      const history = getConversationHistory()
      const sessionId = typeof getSessionId === 'function' ? getSessionId() : null
      if (sessionId) {
        activeRunRef.current = sessionId
        lastSeqRef.current = -1
        writeRunMarker(sessionId, -1)
      }
      ws.send(JSON.stringify({
        type: 'chat',
        message: text,
        session_id: sessionId,
        conversation_history: history.slice(0, -1).filter(m => m.role !== 'system_info'),
      }))

    } catch (error) {
      console.error('WebSocket send error:', error)
      removeLoadingIndicator()
      clearRunMarker(activeRunRef.current)
      activeRunRef.current = null
      dispatch({
        type: Actions.ADD_DISPLAY_MESSAGE,
        payload: { type: 'system', content: `❌ Error: ${error.message}` },
      })
      dispatch({ type: Actions.SET_SENDING, payload: false })
    }
  }, [dispatch, connect, addLoadingIndicator, removeLoadingIndicator, getConversationHistory, getSessionId])

  const stopRun = useCallback(() => {
    const sid = activeRunRef.current || (typeof getSessionId === 'function' ? getSessionId() : null)
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      // No socket to carry the stop — at least unblock the UI.
      removeLoadingIndicator()
      dispatch({ type: Actions.SET_SENDING, payload: false })
      return
    }
    dispatch({ type: Actions.SET_LOADING, payload: 'Stopping…' })
    try {
      ws.send(JSON.stringify({ type: 'stop', session_id: sid }))
    } catch (e) {
      console.error('Stop send failed:', e)
    }
  }, [dispatch, getSessionId, removeLoadingIndicator])

  // Re-attach to a session's run after mount (HA panel iframe was destroyed)
  // or after switching back to it. The marker's lastSeq matches what was saved
  // into the session before leaving, so replay continues without duplicates.
  // Without a marker (other device), attach live from the current seq.
  const resumeSession = useCallback(async (sessionId) => {
    if (!sessionId) return false
    const gen = ++resumeGenRef.current
    const marker = readRunMarker(sessionId)
    let st = null
    try {
      st = await (await fetch(`api/runs/${sessionId}`)).json()
    } catch { /* offline — fall back to the marker */ }
    // The user switched sessions, reset, or sent a message while we waited.
    if (gen !== resumeGenRef.current) return false
    if (typeof getSessionId === 'function' && getSessionId() !== sessionId) return false

    let fromSeq
    if (st && st.status === 'running') {
      fromSeq = marker ? marker.lastSeq : st.last_seq
    } else if (marker && (!st || (st.status !== 'none' && st.last_seq > marker.lastSeq))) {
      // Finished while away. The backend persister saves the final answer into
      // the session; replay only when the loaded history doesn't have it yet.
      const hist = getConversationHistory() || []
      const last = hist[hist.length - 1]
      if (last && last.role === 'assistant' && last.content && !last.tool_calls) {
        clearRunMarker(sessionId)
        return false
      }
      fromSeq = marker.lastSeq
    } else {
      clearRunMarker(sessionId)
      return false
    }

    activeRunRef.current = sessionId
    lastSeqRef.current = typeof fromSeq === 'number' ? fromSeq : -1
    dispatch({ type: Actions.SET_SENDING, payload: true })
    if (!loadingIndicatorIdRef.current) addLoadingIndicator('Reconnecting — AI was working on this conversation…')
    const ws = connect()
    if (ws && ws.readyState === WebSocket.OPEN) {
      // Already open — onopen won't fire again, send the resume directly.
      try {
        ws.send(JSON.stringify({ type: 'resume', session_id: sessionId, last_seq: lastSeqRef.current }))
      } catch (e) {
        console.error('Resume send failed:', e)
      }
    }
    return true
  }, [dispatch, addLoadingIndicator, connect, getConversationHistory, getSessionId])

  const resetWS = useCallback(() => {
    resumeGenRef.current++
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.onerror = null
      wsRef.current.onmessage = null
      try { wsRef.current.close() } catch (e) {}
      wsRef.current = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    removeLoadingIndicator()
    streamingIdRef.current = null
    breadcrumbsMsgIdRef.current = null
    toolCallArgumentsRef.current = {}
    // Detach from the run view — the run keeps executing server-side; its
    // per-session marker stays so resumeSession() can re-attach later.
    activeRunRef.current = null
    lastSeqRef.current = -1
    dispatch({ type: Actions.SET_SENDING, payload: false })
    setIsConnected(false)
  }, [dispatch, removeLoadingIndicator])

  // Keep refs current so circular dependencies don't cause stale closures
  connectRef.current = connect
  handleMessageRef.current = handleMessage

  return { sendMessage, stopRun, resetWS, isConnected, connect, resumeSession }
}
