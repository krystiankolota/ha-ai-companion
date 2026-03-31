import { useAppContext } from '../../store/AppContext'

export default function LoadingMessage() {
  const { state } = useAppContext()
  const { loadingStatus } = state

  return (
    <div className="flex items-start gap-3 px-4 py-3 my-1">
      <div className="flex flex-col gap-1">
        <div className="flex gap-1">
          <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        {loadingStatus && (
          <span className="text-gray-500 text-xs">{loadingStatus}</span>
        )}
      </div>
    </div>
  )
}
