import test from 'node:test'
import assert from 'node:assert/strict'
import { shrinkImage } from './images.js'

test('decodes gallery files without MIME metadata or createImageBitmap', async () => {
  const revoked = []
  const sizes = []
  const original = {
    Image: globalThis.Image,
    document: globalThis.document,
    createObjectURL: URL.createObjectURL,
    revokeObjectURL: URL.revokeObjectURL,
  }
  URL.createObjectURL = () => 'blob:gallery'
  URL.revokeObjectURL = url => revoked.push(url)
  globalThis.Image = class {
    naturalWidth = 3200
    naturalHeight = 1600
    set src(url) { queueMicrotask(() => this.onload()) }
  }
  globalThis.document = {
    createElement() {
      return {
        getContext: () => ({ fillRect() {}, drawImage() {} }),
        toDataURL(type, quality) {
          sizes.push([this.width, this.height, type, quality])
          return 'data:image/jpeg;base64,encoded'
        },
      }
    },
  }
  try {
    assert.equal(await shrinkImage(new Blob(['image'], { type: '' })), 'data:image/jpeg;base64,encoded')
    assert.deepEqual(sizes, [[1568, 784, 'image/jpeg', 0.85]])
    assert.deepEqual(revoked, ['blob:gallery'])

    globalThis.Image = class {
      set src(url) { queueMicrotask(() => this.onerror()) }
    }
    await assert.rejects(shrinkImage(new Blob(['invalid'])), /supported image/)
    assert.deepEqual(revoked, ['blob:gallery', 'blob:gallery'])
  } finally {
    globalThis.Image = original.Image
    globalThis.document = original.document
    URL.createObjectURL = original.createObjectURL
    URL.revokeObjectURL = original.revokeObjectURL
  }
})
