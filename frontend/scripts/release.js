/**
 * Gated release-prep — runs the mechanical, verifiable steps in one fail-fast
 * command so they can't be missed (the 1.18.0/1.18.1/1.18.2 regressions came
 * from skipping build / shipping a 0 default). Aborts on the first failure and
 * commits NOTHING — docs, /review, and git are intentionally left to you.
 *
 *   npm run release -- 1.18.3
 *
 * Runs: version:bump -> build (rebuild+sync) -> check:release -> pytest.
 */
import { execSync } from 'child_process'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const version = process.argv[2]
if (!version || !/^\d+\.\d+\.\d+$/.test(version)) {
  console.error('Usage: npm run release -- <major.minor.patch>')
  process.exit(1)
}

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = resolve(frontend, '..')
const addon = resolve(repo, 'ha-ai-companion')

const run = (cmd, cwd) => {
  console.log(`\n$ ${cmd}   (cwd: ${cwd})`)
  execSync(cmd, { stdio: 'inherit', cwd, shell: true })
}

try {
  run(`npm run version:bump -- ${version}`, frontend)
  run(`npm run build`, frontend)
  run(`npm run check:release`, frontend)
  run(`python -m pytest tests/ -q`, addon)
} catch (e) {
  console.error('\n✗ Release prep FAILED — fix the step above. Nothing was committed.')
  process.exit(1)
}

console.log(`\n✓ Release prep ${version} passed — bumped, built, guard ✓, tests ✓.`)
console.log('Next (manual): update CHANGELOG (both files) + /review the logic diff,')
console.log('then branch → commit → PR → squash-merge → tag vX.Y.Z → gh release create.')
