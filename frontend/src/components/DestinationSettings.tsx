/**
 * Where captures get forwarded, and proving you are allowed to send them there.
 *
 * A destination is created switched off and forwards nothing until it has been
 * verified. That is not bureaucracy: the SSRF rules stop Hooklab being aimed at
 * *our* network, but they say nothing about it being aimed at a stranger's
 * public site, which would make us a free amplifier — their server takes the
 * load, from our IP, and our IP takes the blame. The echo is what separates
 * "a URL I typed" from "a URL I control".
 *
 * Destination URLs are shown as text and never as links. They are user-supplied
 * rather than attacker-supplied, so this is a weaker case than the capture views
 * — but the page's URL carries the view token, so any outbound link leaks it in
 * the `Referer`, and having one rule instead of two is what keeps the rule.
 */

import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'
import type { Destination, VerificationResult } from '../api/types'
import { destinationStatus } from './destinationStatus'

/**
 * Mirrors `CHALLENGE_HEADER` in `app/services/verification.py`.
 *
 * Duplicated deliberately. The backend puts the header name into the sentence it
 * returns when verification fails, but these instructions have to be readable
 * *before* the first attempt, when there is no such sentence yet.
 */
const CHALLENGE_HEADER = 'x-hooklab-verification'

function Instructions({ token }: { token: string }) {
  return (
    <div className="mt-2 rounded bg-slate-50 p-3 text-xs dark:bg-slate-900">
      <p className="text-slate-600 dark:text-slate-400">
        Hooklab will POST to this URL with the header{' '}
        <code className="font-mono text-slate-800 dark:text-slate-200">{CHALLENGE_HEADER}</code>.
        Answer 2xx with the body set to exactly this token, or with that same header set to it:
      </p>
      {/* Shown as selectable text. It is a challenge, not a credential: it grants
          nothing, and someone who cannot read it cannot echo it back. */}
      <code className="mt-2 block break-all font-mono text-slate-900 dark:text-slate-100">
        {token}
      </code>
      <p className="mt-2 text-slate-500">
        The answer has to be the token and nothing else. A service that merely reflects requests
        back would otherwise let anyone verify a URL they do not own.
      </p>
    </div>
  )
}

interface RowProps {
  destination: Destination
  onVerify: () => Promise<VerificationResult>
  onDelete: () => Promise<void>
}

function DestinationRow({ destination, onVerify, onDelete }: RowProps) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<VerificationResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const status = destinationStatus(destination, new Date())

  async function verify() {
    setBusy(true)
    setError(null)
    try {
      setResult(await onVerify())
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not reach the destination.')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      await onDelete()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not remove the destination.')
      setBusy(false)
    }
  }

  return (
    <li className="border-t border-slate-200 py-3 first:border-t-0 dark:border-slate-800">
      <div className="flex items-start gap-2">
        {/* Text, not a link: see the note at the top of this file. */}
        <span className="min-w-0 flex-1 break-all font-mono text-xs text-slate-800 dark:text-slate-200">
          {destination.target_url}
        </span>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium ${status.tone}`}>
          {status.label}
        </span>
      </div>

      <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">{status.detail}</p>

      {status.state === 'unverified' && <Instructions token={destination.verification_token} />}

      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={() => void verify()}
          disabled={busy}
          className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-40 dark:border-slate-700"
        >
          {busy ? 'Checking…' : destination.verified ? 'Check again' : 'Verify'}
        </button>
        <button
          type="button"
          onClick={() => void remove()}
          disabled={busy}
          className="rounded border border-slate-300 px-2 py-1 text-xs text-red-700 disabled:opacity-40 dark:border-slate-700 dark:text-red-400"
        >
          Remove
        </button>
      </div>

      {result && (
        <p
          className={`mt-2 text-xs ${
            result.verified
              ? 'text-emerald-700 dark:text-emerald-400'
              : 'text-amber-700 dark:text-amber-400'
          }`}
        >
          {/* Verbatim. The backend writes these to be read by whoever has to fix
              the destination, and paraphrasing throws away the diagnosis. */}
          {result.detail}
        </p>
      )}

      {error && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>}
    </li>
  )
}

export function DestinationSettings({ viewToken }: { viewToken: string }) {
  const [destinations, setDestinations] = useState<Destination[] | null>(null)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reloading is a key bump rather than a function that writes state directly,
  // matching `useEventStream`. The effect owns every write and carries the
  // `live` guard, so a response that arrives after this panel closed -- or after
  // the token changed -- cannot land in state that no longer belongs to it.
  const [reloadKey, setReloadKey] = useState(0)
  const reload = useCallback(() => setReloadKey((n) => n + 1), [])

  useEffect(() => {
    let live = true

    api
      .listDestinations(viewToken)
      .then((loaded) => live && setDestinations(loaded))
      .catch(() => live && setError('Could not load the destinations.'))

    return () => {
      live = false
    }
  }, [viewToken, reloadKey])

  async function add() {
    if (!url.trim()) return
    setBusy(true)
    setError(null)
    try {
      await api.createDestination(viewToken, url.trim())
      setUrl('')
      reload()
    } catch (failure) {
      // Shown as the backend wrote it. A rejected address explains *which* rule
      // it broke -- private range, loopback, a name that resolves somewhere it
      // should not -- and that sentence is the entire value of the check.
      setError(failure instanceof Error ? failure.message : 'Could not add the destination.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="px-5 py-4">
      <h3 className="text-sm font-semibold">Forwarding</h3>
      <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
        Send every capture on to your own server, with retries and exponential backoff. A
        destination forwards nothing until it has echoed its token back.
      </p>

      <div className="mt-3 flex gap-2">
        <input
          type="url"
          value={url}
          placeholder="https://your-app.example.com/webhooks"
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && void add()}
          className="min-w-0 flex-1 rounded border border-slate-300 bg-white px-2 py-1 font-mono text-sm dark:border-slate-700 dark:bg-slate-900"
        />
        <button
          type="button"
          onClick={() => void add()}
          disabled={busy || !url.trim()}
          className="shrink-0 rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
        >
          {busy ? 'Adding…' : 'Add'}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>}

      {destinations === null ? (
        <p className="mt-3 text-xs text-slate-500">Loading…</p>
      ) : destinations.length === 0 ? (
        <p className="mt-3 text-xs text-slate-500">
          No destinations yet. Captures are still recorded — they just are not forwarded anywhere.
        </p>
      ) : (
        <ul className="mt-3">
          {destinations.map((destination) => (
            <DestinationRow
              key={destination.id}
              destination={destination}
              onVerify={async () => {
                const result = await api.verifyDestination(viewToken, destination.id)
                // A successful verification flips `verified`, so the row's status
                // is stale the moment this returns. Reloading is cheaper than
                // reconciling, and the result itself is still shown by the row.
                reload()
                return result
              }}
              onDelete={async () => {
                await api.deleteDestination(viewToken, destination.id)
                reload()
              }}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
