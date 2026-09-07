/**
 * Everything for one endpoint.
 *
 * Mounted with `key={viewToken}`, so switching endpoints remounts rather than
 * mutating. That is not a style preference: it means no state can survive the
 * switch, so there is no window where the header shows one endpoint's URL while
 * the list shows another's — and it removes the "reset everything on change"
 * effect that would otherwise cause a second render on every navigation.
 */

import { useEffect, useState } from 'react'

import { api } from '../api/client'
import { useEventStream } from '../hooks/useEventStream'
import { RequestDetail } from './RequestDetail'
import { RequestList } from './RequestList'

const STATUS_LABEL = {
  connecting: 'connecting…',
  live: 'listening',
  error: 'disconnected',
} as const

const STATUS_TONE = {
  connecting: 'text-slate-500',
  live: 'text-emerald-600 dark:text-emerald-400',
  error: 'text-red-600 dark:text-red-400',
} as const

function IngestUrl({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)

  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(url)
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1500)
      }}
      className="min-w-0 flex-1 truncate rounded bg-slate-100 px-2 py-1 text-left font-mono text-xs hover:bg-slate-200 dark:bg-slate-900 dark:hover:bg-slate-800"
      title="Copy the ingest URL"
    >
      {copied ? 'Copied' : url}
    </button>
  )
}

export function EndpointView({ viewToken }: { viewToken: string }) {
  const [ingestUrl, setIngestUrl] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { requests, status, missedTooMuch, reload } = useEventStream(viewToken)

  // The ingest URL does not travel on the stream, so it is fetched once. No
  // reset on the way in: a remount already guarantees it starts null.
  useEffect(() => {
    let live = true
    api
      .getEndpoint(viewToken)
      .then((endpoint) => live && setIngestUrl(endpoint.ingest_url))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [viewToken])

  return (
    <div className="flex h-screen flex-col bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="flex items-center gap-3 border-b border-slate-200 px-4 py-2 dark:border-slate-800">
        <span className="shrink-0 text-sm font-semibold">Hooklab</span>
        {ingestUrl && <IngestUrl url={ingestUrl} />}
        <span className={`shrink-0 text-xs ${STATUS_TONE[status]}`}>{STATUS_LABEL[status]}</span>
      </header>

      {missedTooMuch && (
        <div className="flex items-center gap-3 bg-amber-100 px-4 py-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          <span>Too many captures arrived while this tab was away to replay one by one.</span>
          <button type="button" onClick={reload} className="font-semibold underline">
            Reload the list
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="w-1/2 min-w-0 overflow-y-auto border-r border-slate-200 dark:border-slate-800">
          <RequestList requests={requests} selectedId={selectedId} onSelect={setSelectedId} />
        </div>

        <div className="w-1/2 min-w-0 overflow-y-auto">
          {selectedId === null ? (
            <p className="p-6 text-sm text-slate-500">
              Pick a capture to see everything it carried.
            </p>
          ) : (
            // Keyed too, so selecting a different capture starts from a clean
            // slate instead of briefly showing the previous one's body.
            <RequestDetail key={selectedId} viewToken={viewToken} id={selectedId} />
          )}
        </div>
      </div>
    </div>
  )
}
