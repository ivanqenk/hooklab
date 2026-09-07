import type { RequestSummary } from '../api/types'
import { SignatureBadge } from './SignatureBadge'

const METHOD_TONE: Record<string, string> = {
  GET: 'text-sky-700 dark:text-sky-400',
  POST: 'text-emerald-700 dark:text-emerald-400',
  PUT: 'text-amber-700 dark:text-amber-400',
  PATCH: 'text-amber-700 dark:text-amber-400',
  DELETE: 'text-red-700 dark:text-red-400',
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour12: false })
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

interface Props {
  requests: RequestSummary[]
  selectedId: number | null
  onSelect: (id: number) => void
}

export function RequestList({ requests, selectedId, onSelect }: Props) {
  if (requests.length === 0) {
    return (
      <p className="p-6 text-sm text-slate-500">
        Nothing captured yet. Send a request to the ingest URL above and it will appear here.
      </p>
    )
  }

  return (
    <ul className="divide-y divide-slate-200 dark:divide-slate-800">
      {requests.map((request) => (
        <li key={request.id}>
          <button
            type="button"
            onClick={() => onSelect(request.id)}
            aria-current={request.id === selectedId}
            className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800 ${
              request.id === selectedId ? 'bg-slate-100 dark:bg-slate-800' : ''
            }`}
          >
            <span
              className={`w-14 shrink-0 font-mono text-xs font-semibold ${
                METHOD_TONE[request.method] ?? 'text-slate-600 dark:text-slate-400'
              }`}
            >
              {request.method}
            </span>

            {/*
              The path comes from whoever sent the webhook, so it is attacker
              controlled. React escapes it on the way in; `truncate` keeps a
              deliberately enormous one from wrecking the layout.
            */}
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-700 dark:text-slate-300">
              {request.path || '/'}
            </span>

            <SignatureBadge signature={request.signature} />

            <span className="shrink-0 text-xs tabular-nums text-slate-500">
              {size(request.body_size)}
            </span>
            <span className="shrink-0 font-mono text-xs tabular-nums text-slate-400">
              {time(request.received_at)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
