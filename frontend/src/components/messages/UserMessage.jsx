export default function UserMessage({ content, images }) {
  return (
    <div className="flex justify-end">
      <div className="bg-indigo-900 text-gray-100 px-4 py-3 rounded-2xl rounded-br-sm max-w-[80%] break-words">
        {images?.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {images.map((url, i) => (
              <img key={i} src={url} alt={`Attachment ${i + 1}`} className="max-h-40 max-w-full rounded-lg" />
            ))}
          </div>
        )}
        {content}
      </div>
    </div>
  )
}
