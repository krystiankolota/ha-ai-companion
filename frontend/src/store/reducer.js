import { generateSessionId, generateId } from '../lib/utils'

export const initialState = {
  sessions: [],
  currentSessionId: generateSessionId(),
  conversationHistory: [],   // LLM format messages (never rendered directly)
  displayMessages: [],       // { id, type, ...props } — what gets rendered
  streamingContent: '',      // accumulates token by token
  isStreaming: false,
  loadingStatus: null,       // string status text or null
  isSending: false,
  pendingChangeset: null,
  activeDiffChangeset: null, // changeset currently shown in DiffModal
  tokenStats: { input: 0, output: 0, cached: 0 },
  costUsd: 0,
  activeTab: 'chat',
  sidebarOpen: false,
  showClearAllModal: false,
  chatPrefill: null,         // message to prefill in the chat textarea
}

export const Actions = {
  SET_SESSIONS: 'SET_SESSIONS',
  SELECT_SESSION: 'SELECT_SESSION',
  ADD_DISPLAY_MESSAGE: 'ADD_DISPLAY_MESSAGE',
  UPDATE_STREAMING: 'UPDATE_STREAMING',
  COMPLETE_STREAMING: 'COMPLETE_STREAMING',
  SET_LOADING: 'SET_LOADING',
  SET_SENDING: 'SET_SENDING',
  SET_PENDING_CHANGESET: 'SET_PENDING_CHANGESET',
  SET_ACTIVE_DIFF: 'SET_ACTIVE_DIFF',
  UPDATE_TOKEN_STATS: 'UPDATE_TOKEN_STATS',
  SET_ACTIVE_TAB: 'SET_ACTIVE_TAB',
  TOGGLE_SIDEBAR: 'TOGGLE_SIDEBAR',
  CLOSE_SIDEBAR: 'CLOSE_SIDEBAR',
  NEW_CHAT: 'NEW_CHAT',
  PUSH_TO_HISTORY: 'PUSH_TO_HISTORY',
  RESET_CONVERSATION_HISTORY: 'RESET_CONVERSATION_HISTORY',
  REMOVE_LOADING: 'REMOVE_LOADING',
  UPDATE_DISPLAY_MESSAGE: 'UPDATE_DISPLAY_MESSAGE',
  SHOW_CLEAR_ALL_MODAL: 'SHOW_CLEAR_ALL_MODAL',
  HIDE_CLEAR_ALL_MODAL: 'HIDE_CLEAR_ALL_MODAL',
  SET_CHAT_PREFILL: 'SET_CHAT_PREFILL',
  UPDATE_BREADCRUMBS: 'UPDATE_BREADCRUMBS',
  APPEND_BREADCRUMB_STEPS: 'APPEND_BREADCRUMB_STEPS',
}

export function reducer(state, action) {
  switch (action.type) {
    case Actions.SET_SESSIONS:
      return { ...state, sessions: action.payload }

    case Actions.SELECT_SESSION:
      return {
        ...state,
        currentSessionId: action.payload.id,
        conversationHistory: action.payload.messages || [],
        displayMessages: action.payload.displayMessages || [],
        streamingContent: '',
        isStreaming: false,
        loadingStatus: null,
        isSending: false,
        pendingChangeset: null,
        activeDiffChangeset: null,
        tokenStats: { input: 0, output: 0, cached: 0 },
        costUsd: 0,
      }

    case Actions.ADD_DISPLAY_MESSAGE: {
      // Remove any existing loading message before adding another if not already one
      const newMsg = { id: generateId(), ...action.payload }
      return {
        ...state,
        displayMessages: [...state.displayMessages, newMsg],
      }
    }

    case Actions.UPDATE_STREAMING: {
      const newContent = state.streamingContent + action.payload
      // Find existing streaming message or we'll rely on ADD_DISPLAY_MESSAGE to create it
      const msgs = state.displayMessages.map(m =>
        m.isStreaming ? { ...m, content: newContent } : m
      )
      return {
        ...state,
        streamingContent: newContent,
        isStreaming: true,
        displayMessages: msgs,
      }
    }

    case Actions.COMPLETE_STREAMING: {
      // Finalize streaming message (isStreaming -> false)
      const msgs = state.displayMessages.map(m =>
        m.isStreaming ? { ...m, isStreaming: false, content: state.streamingContent } : m
      )
      return {
        ...state,
        streamingContent: '',
        isStreaming: false,
        displayMessages: msgs,
      }
    }

    case Actions.SET_LOADING:
      if (action.payload === null) {
        // Remove loading message
        return {
          ...state,
          loadingStatus: null,
          displayMessages: state.displayMessages.filter(m => m.type !== 'loading'),
        }
      }
      // Add loading message if not present, update status
      {
        const hasLoading = state.displayMessages.some(m => m.type === 'loading')
        const msgs = hasLoading
          ? state.displayMessages
          : [...state.displayMessages, { id: generateId(), type: 'loading' }]
        return { ...state, loadingStatus: action.payload, displayMessages: msgs }
      }

    case Actions.REMOVE_LOADING:
      return {
        ...state,
        loadingStatus: null,
        displayMessages: state.displayMessages.filter(m => m.type !== 'loading'),
      }

    case Actions.SET_SENDING:
      return { ...state, isSending: action.payload }

    case Actions.SET_PENDING_CHANGESET:
      return { ...state, pendingChangeset: action.payload }

    case Actions.SET_ACTIVE_DIFF:
      return { ...state, activeDiffChangeset: action.payload }

    case Actions.UPDATE_TOKEN_STATS:
      return {
        ...state,
        tokenStats: {
          input: state.tokenStats.input + (action.payload.input || 0),
          output: state.tokenStats.output + (action.payload.output || 0),
          cached: state.tokenStats.cached + (action.payload.cached || 0),
        },
        costUsd: state.costUsd + (action.payload.costUsd || 0),
      }

    case Actions.SET_ACTIVE_TAB:
      return { ...state, activeTab: action.payload }

    case Actions.TOGGLE_SIDEBAR:
      return { ...state, sidebarOpen: !state.sidebarOpen }

    case Actions.CLOSE_SIDEBAR:
      return { ...state, sidebarOpen: false }

    case Actions.NEW_CHAT:
      return {
        ...state,
        currentSessionId: generateSessionId(),
        conversationHistory: [],
        displayMessages: [],
        streamingContent: '',
        isStreaming: false,
        loadingStatus: null,
        isSending: false,
        pendingChangeset: null,
        activeDiffChangeset: null,
        tokenStats: { input: 0, output: 0, cached: 0 },
        costUsd: 0,
        sidebarOpen: false,
      }

    case Actions.PUSH_TO_HISTORY: {
      let history = [...state.conversationHistory, action.payload]
      // Cap at 200
      if (history.length > 200) history = history.slice(-200)
      return { ...state, conversationHistory: history }
    }

    case Actions.RESET_CONVERSATION_HISTORY:
      return { ...state, conversationHistory: action.payload }

    case Actions.UPDATE_DISPLAY_MESSAGE:
      return {
        ...state,
        displayMessages: state.displayMessages.map(m =>
          m.id === action.payload.id ? { ...m, ...action.payload.updates } : m
        ),
      }

    case Actions.SHOW_CLEAR_ALL_MODAL:
      return { ...state, showClearAllModal: true }

    case Actions.HIDE_CLEAR_ALL_MODAL:
      return { ...state, showClearAllModal: false }

    case Actions.SET_CHAT_PREFILL:
      return { ...state, chatPrefill: action.payload }

    case Actions.UPDATE_BREADCRUMBS: {
      // Update a specific step inside a breadcrumbs message, matched by tool_call_id
      const { id, tool_call_id, updates } = action.payload
      return {
        ...state,
        displayMessages: state.displayMessages.map(m => {
          if (m.id !== id) return m
          return {
            ...m,
            steps: m.steps.map(s =>
              s.tool_call_id === tool_call_id ? { ...s, ...updates } : s
            ),
          }
        }),
      }
    }

    case Actions.APPEND_BREADCRUMB_STEPS: {
      // Append new steps to an existing breadcrumbs message (for multi-iteration turns)
      const { id, steps } = action.payload
      return {
        ...state,
        displayMessages: state.displayMessages.map(m =>
          m.id === id ? { ...m, steps: [...m.steps, ...steps] } : m
        ),
      }
    }

    default:
      return state
  }
}
