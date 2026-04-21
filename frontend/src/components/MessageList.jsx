import { useRef, useEffect } from 'react'
import { useAppContext } from '../store/AppContext'
import UserMessage from './messages/UserMessage'
import AssistantMessage from './messages/AssistantMessage'
import SystemMessage from './messages/SystemMessage'
import ToolCallMessage from './messages/ToolCallMessage'
import ToolResultMessage from './messages/ToolResultMessage'
import ApprovalCard from './messages/ApprovalCard'
import BreadcrumbsMessage from './messages/BreadcrumbsMessage'
import LoadingMessage from './messages/LoadingMessage'

function MessageItem({ msg }) {
  switch (msg.type) {
    case 'user':
      return <UserMessage content={msg.content} />
    case 'assistant':
      return <AssistantMessage content={msg.content} isStreaming={msg.isStreaming} />
    case 'system':
      return <SystemMessage content={msg.content} />
    case 'tool_call':
      return <ToolCallMessage toolNames={msg.toolNames} toolCalls={msg.toolCalls} />
    case 'tool_result':
      return <ToolResultMessage functionName={msg.functionName} result={msg.result} />
    case 'approval':
      return <ApprovalCard changeset={msg.changeset} />
    case 'breadcrumbs':
      return <BreadcrumbsMessage steps={msg.steps} />
    case 'save_notification':
      return (
        <div className="flex items-center gap-1.5 text-xs text-emerald-400 px-1 py-0.5 my-0.5">
          <span>💾</span>
          <span>Saved memory{msg.filename ? `: ${msg.filename}` : ''}</span>
        </div>
      )
    case 'loading':
      return <LoadingMessage />
    default:
      return null
  }
}

export default function MessageList() {
  const { state } = useAppContext()
  const { displayMessages } = state
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [displayMessages.length])

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
      {displayMessages.length === 0 && (
        <div className="flex flex-col items-center justify-center h-full text-gray-600 text-sm select-none">
          <div className="text-4xl mb-3">🏠</div>
          <div className="font-medium text-gray-500">HA AI Companion</div>
          <div className="text-xs mt-1">Ask me anything about your Home Assistant</div>
        </div>
      )}
      {displayMessages.map(msg => (
        <MessageItem key={msg.id} msg={msg} />
      ))}
      <div ref={endRef} />
    </div>
  )
}
