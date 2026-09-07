import { useEffect, useState } from 'react'

import { api } from './api/client'
import type { EndpointCreated } from './api/types'
import { EndpointView } from './components/EndpointView'

/**
 * The view token lives in the URL fragment.
 *
 * A fragment rather than a path segment or a query string, deliberately:
 * fragments are never sent to the server, so the token stays out of access logs
 * and out of every proxy in between. It is still visible in the address bar and
 * in history — unavoidable while the token *is* the credential — which is why
 * `no-referrer` is set in index.html.
 */
function tokenFromUrl(): string | null {
  return window.location.hash.slice(1) || null
}

function Welcome({ onCreated }: { onCreated: (endpoint: EndpointCreated) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function create() {
    setBusy(true)
    setError(null)
    try {
      onCreated(await api.createEndpoint('Web session'))
    } catch {
      setError('Could not reach the API. Is the backend running on port 8010?')
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto mt-24 max-w-lg px-6 text-center">
      <h1 className="text-2xl font-semibold">Hooklab</h1>
      <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
        Get a public URL, point a provider at it, and watch every request arrive — with its
        signature checked and the reason spelled out when it fails.
      </p>

      <button
        type="button"
        onClick={create}
        disabled={busy}
        className="mt-6 rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
      >
        {busy ? 'Creating…' : 'Create an endpoint'}
      </button>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <p className="mt-8 text-xs text-slate-500">
        No sign-up. Anonymous endpoints expire after 72 hours, and the link in your address bar is
        the only way back in — keep it if you want to return.
      </p>
    </div>
  )
}

export default function App() {
  const [viewToken, setViewToken] = useState<string | null>(tokenFromUrl)

  // Back and forward have to keep working: the fragment is the state.
  useEffect(() => {
    const onNavigate = () => setViewToken(tokenFromUrl())
    window.addEventListener('hashchange', onNavigate)
    return () => window.removeEventListener('hashchange', onNavigate)
  }, [])

  if (!viewToken) {
    return (
      <Welcome
        onCreated={(endpoint) => {
          window.location.hash = endpoint.view_token
          setViewToken(endpoint.view_token)
        }}
      />
    )
  }

  // Keyed by the token, so switching endpoints remounts everything below rather
  // than leaving one endpoint's state visible against another's data.
  return <EndpointView key={viewToken} viewToken={viewToken} />
}
