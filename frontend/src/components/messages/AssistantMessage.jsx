import { renderMarkdown } from '../../lib/utils'

export default function AssistantMessage({ content, isStreaming }) {
  return (
    <div className="flex justify-start">
      <div className="bg-surface-900 border border-surface-700 px-4 py-3 rounded-2xl rounded-bl-sm max-w-[85%] break-words">
        {isStreaming ? (
          <span className="text-gray-200 whitespace-pre-wrap">
            {content}
            <span className="inline-block w-0.5 h-4 bg-indigo-400 ml-0.5 align-middle animate-pulse" />
          </span>
        ) : (
          <div
            className="prose"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
          />
        )}
      </div>
    </div>
  )
}
