/**
 * Release guard — fails if the built bundle does not match the version.
 *
 * The HTML template requests `static/dist/bundle.<version>.js`. If a version
 * bump is followed by `npm run sync` instead of `npm run build`, the dist still
 * holds the OLD bundle → the page 404s on `bundle.<new>.js` → blank white screen.
 * This caught us in 1.18.0. Run this before every commit/release:
 *   npm run check:release
 *
 * Exits non-zero (and prints what is wrong) so CI / a pre-commit hook can block.
 */
import { readFileSync, existsSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

// Source of truth for the served version.
const configYaml = readFileSync(resolve(root, 'ha-ai-companion/config.yaml'), 'utf8')
const m = configYaml.match(/^version: "(.+)"/m)
if (!m) {
  console.error('✗ Could not read version from ha-ai-companion/config.yaml')
  process.exit(1)
}
const version = m[1]

// Both dist locations must carry the version-matched bundle (add-on + HACS).
const distDirs = [
  'ha-ai-companion/static/dist',
  'custom_components/ha_ai_companion/static/dist',
]

const problems = []
for (const dir of distDirs) {
  for (const ext of ['js', 'css']) {
    const rel = `${dir}/bundle.${version}.${ext}`
    if (!existsSync(resolve(root, rel))) problems.push(`missing ${rel}`)
  }
}

// Cross-check the 3 version files agree (bump-version writes all three).
const mainPy = readFileSync(resolve(root, 'ha-ai-companion/src/main.py'), 'utf8')
if (!new RegExp(`^version = "${version.replace(/\./g, '\\.')}"`, 'm').test(mainPy)) {
  problems.push(`ha-ai-companion/src/main.py version != ${version}`)
}
const manifest = JSON.parse(readFileSync(resolve(root, 'custom_components/ha_ai_companion/manifest.json'), 'utf8'))
if (manifest.version !== version) problems.push(`manifest.json version (${manifest.version}) != ${version}`)

if (problems.length) {
  console.error(`✗ Release check FAILED for version ${version}:`)
  for (const p of problems) console.error(`   - ${p}`)
  console.error('\nFix: run `npm run build` (NOT `npm run sync`) after a version bump.')
  process.exit(1)
}

console.log(`✓ Release check passed — bundle.${version}.{js,css} present in both dist dirs, versions aligned.`)
