/**
 * Turning signature verification on for an endpoint.
 *
 * Two properties of the secret drive the whole shape of this form.
 *
 * **It is write-only.** The backend encrypts it and returns it from no route, in
 * no form -- not masked, absent. So there is nothing to load into the field, and
 * the honest thing is to say that rather than render a row of dots that suggests
 * a value is there to be edited. Changing the secret means sending a new one.
 *
 * **It is configured behind the view token, never the ingest one.** The ingest
 * token is public by design and ends up in provider panels, logs and tickets. If
 * it could set the secret, everyone who ever saw the URL could switch
 * verification off or point it at a secret of their own.
 *
 * The value is also dropped from React state the moment it has been sent. It
 * cannot be cleared from the browser's memory in any real sense, but keeping it
 * in a mounted component's state for the rest of the session is a choice, and
 * this is the cheap half of not making it.
 */

import { useState } from 'react'

import { api } from '../api/client'
import type { EndpointPublic, SignatureProvider } from '../api/types'

const PROVIDERS: { value: SignatureProvider; label: string; hint: string }[] = [
  {
    value: 'stripe',
    label: 'Stripe',
    hint: 'The signing secret from the webhook endpoint, starting with whsec_.',
  },
  {
    value: 'github',
    label: 'GitHub',
    hint: 'The secret you typed into the webhook settings for the repository.',
  },
]

interface Props {
  viewToken: string
  endpoint: EndpointPublic
  onChanged: () => void
}

export function SignatureSettings({ viewToken, endpoint, onChanged }: Props) {
  const [provider, setProvider] = useState<SignatureProvider>(
    (endpoint.signature_provider as SignatureProvider | null) ?? 'stripe',
  )
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const configured = endpoint.signature_provider !== null
  const hint = PROVIDERS.find((each) => each.value === provider)?.hint ?? ''

  async function save() {
    if (!secret) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await api.configureSignature(viewToken, provider, secret)
      // Dropped as soon as it is sent, before anything else can go wrong.
      setSecret('')
      setSaved(true)
      onChanged()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not save the secret.')
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await api.clearSignature(viewToken)
      setSecret('')
      onChanged()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not stop verifying.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
      <h3 className="text-sm font-semibold">Signature verification</h3>
      <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
        {configured
          ? `Every capture is checked against your ${endpoint.signature_provider} secret, and the verdict says why when it fails.`
          : 'Give Hooklab the provider’s signing secret and every capture gets checked, with a reason when it does not match.'}
      </p>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-slate-500">Provider</span>
          <select
            value={provider}
            onChange={(event) => setProvider(event.target.value as SignatureProvider)}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
          >
            {PROVIDERS.map((each) => (
              <option key={each.value} value={each.value}>
                {each.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs">
          <span className="text-slate-500">
            {configured ? 'Replace the secret' : 'Signing secret'}
          </span>
          <input
            // A password field, and never populated from the server: there is no
            // value to populate it with, which is the point of the design.
            type="password"
            value={secret}
            autoComplete="off"
            spellCheck={false}
            placeholder={configured ? 'Stored — type a new one to replace it' : 'whsec_…'}
            onChange={(event) => setSecret(event.target.value)}
            className="min-w-0 rounded border border-slate-300 bg-white px-2 py-1 font-mono text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </label>

        <button
          type="button"
          onClick={() => void save()}
          disabled={busy || !secret}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
        >
          {busy ? 'Saving…' : configured ? 'Replace' : 'Start verifying'}
        </button>

        {configured && (
          <button
            type="button"
            onClick={() => void stop()}
            disabled={busy}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40 dark:border-slate-700"
          >
            Stop verifying
          </button>
        )}
      </div>

      <p className="mt-2 text-xs text-slate-500">{hint}</p>

      {saved && (
        <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">
          Saved. Captures from now on are verified — the ones already in the list were taken
          before the secret existed and are not re-checked.
        </p>
      )}

      {error && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>}
    </section>
  )
}
