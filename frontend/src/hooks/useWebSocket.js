import { useRef, useCallback, useState } from 'react'
import { Actions } from '../store/reducer'
import { generateId } from '../lib/utils'
import { getChangeset } from '../lib/api'
import { toolLabel, makeArgsSummary, makeResultSummary } from '../lib/toolLabels'

// Extract original file contents from search_config_files / get_nodered_flows tool calls in conversation history
function extractOriginalContents(history, fileChanges) {
  const originalContents = {}
  const filePathsNeeded = fileChanges.map(fc => fc.file_path)

  // Last get_nodered_flows result — used to populate original for nodered/ paths
  let lastNoderedFlows = null

  for (const msg of history) {
    if (msg.role === 'assistant' && msg.tool_calls) {
      for (const toolCall of msg.tool_calls) {
        const toolResponse = history.find(
          m => m.role === 'tool' && m.tool_call_id === toolCall.id
        )
        if (!toolResponse) continue

        if (toolCall.function.name === 'search_config_files') {
          try {
            const searchResult = JSON.parse(toolResponse.content)
            if (searchResult.success && searchResult.files) {
              for (const file of searchResult.files) {
                if (filePathsNeeded.includes(file.path)) {
                  originalContents[file.path] = file.content
                }
              }
            }
          } catch (e) {
            console.error('Error parsing search results:', e)
          }
        }

        if (toolCall.function.name === 'get_nodered_flows') {
          try {
            const result = JSON.parse(toolResponse.content)
            if (result.success && result.flows) {
              lastNoderedFlows = result.flows
            }
          } catch (e) { /* ignore */ }
        }
      }
    }
  }

  // Populate original content for nodered/ virtual paths from the last get_nodered_flows result
  if (lastNoderedFlows) {
    for (const fp of filePathsNeeded) {
      if (originalContents[fp]) continue
      if (fp === 'nodered/flows.json') {
        originalContents[fp] = JSON.stringify(lastNoderedFlows, null, 2)
      } else if (fp.startsWith('nodered/flow/')) {
        const tabId = fp.replace('nodered/flow/', '').replace('.json', '')
        const tab = lastNoderedFlows.find(n => n.type === 'tab' && n.id === tabId)
        const nodes = lastNoderedFlows.filter(n => n.z === tabId)
        if (tab) {
          originalContents[fp] = JSON.stringify([tab, ...nodes], null, 2)
        }
      }
    }
  }

  return originalContents
}

export function useWebSocket(dispatch, getConversationHistory, onAutoSave) {
  const wsRef = useRef(null)
  const loadingIndicatorIdRef = useRef(null)
  const toolCallArgumentsRef = useRef({})
  const streamingIdRef = useRef(null)
  const breadcrumbsMsgIdRef = useRef(null)  // ID of the active breadcrumbs message for this turn
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef(null)
  const connectRef = useRef(null) // forward ref to break circular dependency
  const handleMessageRef = useRef(null) // forward ref to break circular dependency
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

    try {
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
          streamingIdRef.current = null
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

        // Handle all changeset-returning tools — show approval card
        const CHANGESET_TOOLS_FULL_DIFF = ['propose_config_changes']
        const CHANGESET_TOOLS_FETCH_DIFF = ['patch_config_key', 'patch_config_block', 'add_nodered_flow', 'edit_nodered_tab']
        const isChangesetTool = [...CHANGESET_TOOLS_FULL_DIFF, ...CHANGESET_TOOLS_FETCH_DIFF].includes(data.function)

        if (isChangesetTool && data.result.success && data.result.changeset_id) {
          const changesetData = {
            changeset_id: data.result.changeset_id,
            total_files: data.result.total_files,
            files: data.result.files,
            reason: data.result.reason,
            diff_stats: data.result.diff_stats || [],
          }

          if (CHANGESET_TOOLS_FULL_DIFF.includes(data.function)) {
            // propose_config_changes: extract original contents from history, dispatch immediately
            const history = getConversationHistory()
            const args = data.arguments || toolCallArgumentsRef.current[data.tool_call_id]
            if (args && args.changes) {
              changesetData.file_changes_detail = args.changes
              changesetData.original_contents = extractOriginalContents(history, args.changes)
            } else {
              console.warn('propose_config_changes: no arguments found, approval card will have limited info')
            }
            dispatch({
              type: Actions.ADD_DISPLAY_MESSAGE,
              payload: { type: 'approval', changeset: changesetData },
            })
          } else {
            // Patch/NR tools: fetch new_content from server to build diff.
            // Pre-generate the message id so we can update it after the async fetch.
            const msgId = generateId()
            changesetData._msgId = msgId
            dispatch({
              type: Actions.ADD_DISPLAY_MESSAGE,
              payload: { id: msgId, type: 'approval', changeset: changesetData },
            })
            getChangeset(data.result.changeset_id).then(cs => {
              if (cs && cs.file_changes) {
                const history = getConversationHistory()
                const fileChanges = cs.file_changes
                dispatch({
                  type: Actions.UPDATE_DISPLAY_MESSAGE,
                  payload: {
                    id: msgId,
                    updates: {
                      changeset: {
                        ...changesetData,
                        file_changes_detail: fileChanges,
                        original_contents: extractOriginalContents(history, fileChanges),
                      },
                    },
                  },
                })
              }
            }).catch(err => console.warn('Failed to fetch changeset for diff:', err))
          }
        }

        // Save notification for save_memory
        if (data.function === 'save_memory' && data.result.success) {
          dispatch({
            type: Actions.ADD_DISPLAY_MESSAGE,
            payload: {
              type: 'save_notification',
              filename: data.result.filename || '',
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
        dispatch({ type: Actions.SET_SENDING, payload: false })

        if (typeof onAutoSave === 'function') {
          onAutoSave()
        }

      } else if (eventType === 'error') {
        dispatch({
          type: Actions.ADD_DISPLAY_MESSAGE,
          payload: { type: 'system', content: `❌ Error: ${data.error}` },
        })
        removeLoadingIndicator()
        dispatch({ type: Actions.SET_SENDING, payload: false })
      }
    } catch (e) {
      console.error('Error handling WebSocket message:', e)
    }
  }, [dispatch, removeLoadingIndicator, addLoadingIndicator, getConversationHistory, onAutoSave])

  const sendMessage = useCallback(async (text) => {
    if (!text || !text.trim()) return

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
      ws.send(JSON.stringify({
        type: 'chat',
        message: text,
        conversation_history: history.slice(0, -1).filter(m => m.role !== 'system_info'),
      }))

    } catch (error) {
      console.error('WebSocket send error:', error)
      removeLoadingIndicator()
      dispatch({
        type: Actions.ADD_DISPLAY_MESSAGE,
        payload: { type: 'system', content: `❌ Error: ${error.message}` },
      })
      dispatch({ type: Actions.SET_SENDING, payload: false })
    }
  }, [dispatch, connect, addLoadingIndicator, removeLoadingIndicator, getConversationHistory])

  const resetWS = useCallback(() => {
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
    toolCallArgumentsRef.current = {}
    dispatch({ type: Actions.SET_SENDING, payload: false })
    setIsConnected(false)
  }, [dispatch, removeLoadingIndicator])

  // Keep refs current so circular dependencies don't cause stale closures
  connectRef.current = connect
  handleMessageRef.current = handleMessage

  return { sendMessage, resetWS, isConnected, connect }
}
