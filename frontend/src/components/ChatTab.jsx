import MessageList from './MessageList'
import ChatInput from './ChatInput'

export default function ChatTab({ onSend }) {
  return (
    <div className="flex flex-col h-full">
      <MessageList />
      <ChatInput onSend={onSend} />
    </div>
  )
}
