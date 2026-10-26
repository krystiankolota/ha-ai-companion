// Keep in sync with MAX_CHAT_IMAGES / MAX_CHAT_IMAGE_CHARS in ha-ai-companion/src/main.py.
export const MAX_IMAGES = 3
export const MAX_TOTAL_CHARS = 2_000_000
const MAX_SIDE = 1568 // larger images cost more tokens without helping most vision models

// Downscale and re-encode as JPEG to cut upload size and token cost.
export async function shrinkImage(file) {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#fff' // JPEG has no alpha; transparent PNGs would turn black
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close?.()
  return canvas.toDataURL('image/jpeg', 0.85)
}
