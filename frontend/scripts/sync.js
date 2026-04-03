/**
 * Sync shared files from ha-ai-companion/ (source of truth) to custom_components/
 * Run automatically after `npm run build` via postbuild hook.
 * Also available as `npm run sync` for Python-only changes.
 */
import { cpSync, readdirSync, rmSync, existsSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const addon = resolve(root, 'ha-ai-companion')
const component = resolve(root, 'custom_components/ha_ai_companion')
const opts = { recursive: true, force: true }

// Remove old bundles from the target before copying (both versioned and unversioned)
const targetDist = resolve(component, 'static/dist')
if (existsSync(targetDist)) {
  for (const f of readdirSync(targetDist)) {
    if (/^bundle(\.\d+\.\d+\.\d+)?\.(js|css)$/.test(f)) {
      rmSync(resolve(targetDist, f))
    }
  }
}

cpSync(resolve(addon, 'static/dist'), resolve(component, 'static/dist'), opts)
console.log('✓ static/dist synced')

cpSync(resolve(addon, 'src'), resolve(component, 'src'), opts)
console.log('✓ src/ synced')

cpSync(resolve(addon, 'templates/index.html'), resolve(component, 'templates/index.html'), opts)
console.log('✓ templates/index.html synced')
