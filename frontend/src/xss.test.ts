/**
 * A standing guard on the one rule this UI cannot break.
 *
 * Every page here renders content written by whoever sent the webhook -- headers,
 * query values, bodies -- and the page also carries the view token in its URL. A
 * script that ran here could read that token and every payload behind it.
 *
 * React escapes text nodes, so the danger is not what we write today: it is the
 * plausible future change that reaches for `dangerouslySetInnerHTML` to
 * "pretty-print the body", or renders a URL from a payload as a real link. This
 * test is what makes that change fail loudly instead of shipping.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SOURCE_ROOT = new URL('.', import.meta.url).pathname

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.tsx?$/.test(entry) && !entry.endsWith('.test.ts') ? [path] : []
  })
}

/** Comments explain these rules, so they must not count as breaking them. */
function code(path: string): string {
  return readFileSync(path, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
}

describe('the rendering rules that keep captured content inert', () => {
  const files = sourceFiles(SOURCE_ROOT)

  it('finds the source to check', () => {
    expect(files.length).toBeGreaterThan(4)
  })

  it.each(files.map((path) => [path.replace(SOURCE_ROOT, ''), path]))(
    '%s does not bypass React escaping',
    (_name, path) => {
      const source = code(path)

      expect(source).not.toContain('dangerouslySetInnerHTML')
      expect(source).not.toContain('innerHTML')
      // `srcdoc` renders arbitrary markup inside an iframe. A preview, if one is
      // ever wanted, belongs in a sandboxed frame without allow-same-origin --
      // set up deliberately, never by an attribute that slipped in.
      expect(source).not.toContain('srcdoc')
    },
  )

  it.each(files.map((path) => [path.replace(SOURCE_ROOT, ''), path]))(
    '%s builds no link out of captured content',
    (_name, path) => {
      // Every href in this app has to come from our own API helper. A link built
      // from a payload would send the full URL -- view token included -- to
      // whatever host the attacker chose, in the Referer header.
      for (const [, expression] of code(path).matchAll(/href=\{([^}]*)\}/g)) {
        expect(expression).toContain('api.')
      }
    },
  )

  it.each(files.map((path) => [path.replace(SOURCE_ROOT, ''), path]))(
    '%s keeps credentials out of browser storage',
    (_name, path) => {
      // Two secrets pass through this app: the view token, which unlocks every
      // capture, and the provider's signing secret. Neither is written anywhere
      // that outlives the tab.
      //
      // "Remember my endpoints" is the entirely reasonable feature that
      // introduces this, so the rule is worth stating rather than assuming. The
      // token already survives in the URL fragment, which the user can see and
      // clear; a copy in localStorage is one an XSS can read long afterwards and
      // the user never knew existed.
      const source = code(path)

      expect(source).not.toContain('localStorage')
      expect(source).not.toContain('sessionStorage')
      expect(source).not.toContain('document.cookie')
    },
  )
})
