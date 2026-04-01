/**
 * Bump version in all 3 version files atomically.
 * Usage: npm run version:bump -- 1.1.21
 */
import { readFileSync, writeFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const version = process.argv[2]
if (!version || !/^\d+\.\d+\.\d+$/.test(version)) {
  console.error('Usage: npm run version:bump -- <major.minor.patch>')
  process.exit(1)
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

// ha-ai-companion/config.yaml
const configPath = resolve(root, 'ha-ai-companion/config.yaml')
writeFileSync(configPath, readFileSync(configPath, 'utf8').replace(/^version: ".+"/m, `version: "${version}"`))
console.log(`✓ ha-ai-companion/config.yaml → ${version}`)

// ha-ai-companion/src/main.py (source of truth — sync will propagate to custom_components)
const mainPath = resolve(root, 'ha-ai-companion/src/main.py')
writeFileSync(mainPath, readFileSync(mainPath, 'utf8').replace(/^version = ".+"/m, `version = "${version}"`))
console.log(`✓ ha-ai-companion/src/main.py → ${version}`)

// custom_components/ha_ai_companion/manifest.json
const manifestPath = resolve(root, 'custom_components/ha_ai_companion/manifest.json')
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
manifest.version = version
writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n')
console.log(`✓ custom_components/ha_ai_companion/manifest.json → ${version}`)

console.log(`\nVersion bumped to ${version}. Run 'npm run build' to rebuild and sync.`)
