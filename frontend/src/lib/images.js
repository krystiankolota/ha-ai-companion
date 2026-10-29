// Keep in sync with MAX_CHAT_IMAGES / MAX_CHAT_IMAGE_CHARS in ha-ai-companion/src/main.py.
export const MAX_IMAGES = 3
export const MAX_TOTAL_CHARS = 2_000_000
const MAX_SIDE = 1568 // larger images cost more tokens without helping most vision models

// Downscale and re-encode as JPEG to cut upload size and token cost.
export async function shrinkImage(file) {
  const url = URL.createObjectURL(file)
  try {
    const image = new Image()
    await new Promise((resolve, reject) => {
      image.onload = resolve
      image.onerror = () => reject(new Error('Could not decode a supported image. Try PNG or JPEG.'))
      image.src = url
    })
    const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Image processing is unavailable in this browser.')
    ctx.fillStyle = '#fff' // JPEG has no alpha; transparent PNGs would turn black
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/jpeg', 0.85)
  } finally {
    URL.revokeObjectURL(url)
  }
}
