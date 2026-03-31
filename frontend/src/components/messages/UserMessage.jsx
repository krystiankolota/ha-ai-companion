export default function UserMessage({ content }) {
  return (
    <div className="flex justify-end mb-3">
      <div className="bg-indigo-900 text-gray-100 px-4 py-3 rounded-2xl rounded-br-sm max-w-[80%] break-words">
        {content}
      </div>
    </div>
  )
}
