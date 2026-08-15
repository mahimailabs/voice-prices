/**
 * Parse every docs page with the MDX compiler, so a syntax error fails a pull request
 * instead of a deploy.
 *
 * Mintlify only builds on pushes to main, so a malformed page merges green and then the
 * deploy aborts, leaving the site silently serving the previous version. That happened:
 * `<stt | tts | s2s>` inside a prompt read as broken JSX, Mintlify reported
 * "Unexpected character `|` (U+007C) after name", and the published page stayed a commit
 * behind with no failing check on main to explain why.
 *
 * This only checks that a page *parses*. Undefined components are not an error here, and
 * should not be: MDX resolves components at render time, not compile time, so Mintlify's
 * own <Note>, <Steps> and friends are unresolvable in isolation and still perfectly valid.
 * The class of bug being caught is a raw `<` that MDX reads as a tag.
 */
import { compile } from '@mdx-js/mdx'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import process from 'node:process'

const DOCS_DIR = 'docs'

function mdxFiles(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...mdxFiles(path))
    else if (entry.name.endsWith('.mdx')) out.push(path)
  }
  return out
}

const files = process.argv.length > 2 ? process.argv.slice(2) : mdxFiles(DOCS_DIR).sort()
const failures = []

for (const file of files) {
  try {
    await compile(readFileSync(file, 'utf8'), { format: 'mdx' })
  } catch (error) {
    const where = error.line ? ` (line ${error.line}, column ${error.column})` : ''
    failures.push(`${file}${where}\n    ${error.message}`)
  }
}

if (failures.length) {
  console.error(`${failures.length} of ${files.length} docs page(s) failed to parse:\n`)
  for (const failure of failures) console.error(`  ${failure}\n`)
  console.error('A raw `<` is the usual cause. MDX reads it as a JSX tag, even inside a component body.')
  process.exit(1)
}

console.log(`all ${files.length} docs page(s) parse`)
