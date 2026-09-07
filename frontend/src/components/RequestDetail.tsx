/**
 * One capture, in full.
 *
 * This component renders content written by whoever sent the webhook: headers,
 * query values, the body. That makes stored XSS the real risk on this page, and
 * the page also carries the view token in its URL -- so a script running here
 * could read the token and every payload behind it.
 *
 * Two rules follow, and neither is negotiable:
 *
 * - **No `dangerouslySetInnerHTML` over captured content, ever.** React escapes
 *   by default; that prop is the one way to opt out, and "pretty-print the body"
 *   is exactly the excuse that gets it introduced.
 * - **No links built from captured content.** Rendering one would put the view
 *   token in the `Referer` sent to whatever the attacker chose. Values are shown
 *   as text and can be copied.
 *
 * If HTML preview is ever wanted -- tempting for email webhooks -- it goes in a
 * sandboxed iframe without `allow-same-origin`, never inline.
 */

import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { Delivery, RequestDetail as Detail } from '../api/types'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-slate-200 px-4 py-3 dark:border-slate-800">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h3>
      {children}
    </section>
  )
}

function Pairs({ pairs }: { pairs: Record<string, string> }) {
  const entries = Object.entries(pairs)
  if (entries.length === 0) return <p className="text-sm text-slate-500">None.</p>

  return (
    <dl className="grid grid-cols-[minmax(0,12rem)_1fr] gap-x-3 gap-y-1 font-mono text-xs">
      {entries.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="truncate text-slate-500">{name}</dt>
          {/* break-all: a single header value can be thousands of characters. */}
          <dd className="break-all text-slate-800 dark:text-slate-200">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Body({ detail }: { detail: Detail }) {
  if (detail.body_size === 0) return <p className="text-sm text-slate-500">Empty.</p>

  if (detail.body_encoding === 'base64') {
    return (
      <p className="text-sm text-slate-500">
        The body is not valid UTF-8 — {detail.body_size} bytes of binary. Download it to inspect.
      </p>
    )
  }

  // Pretty-printed when it parsed as JSON, raw otherwise. Either way it lands in
  // a text node that React escapes.
  const shown =
    detail.body_json !== null && detail.body_json !== undefined
      ? JSON.stringify(detail.body_json, null, 2)
      : (detail.body_text ?? '')

  return (
    <pre className="max-h-96 overflow-auto rounded bg-slate-50 p-3 font-mono text-xs text-slate-800 dark:bg-slate-900 dark:text-slate-200">
      {shown}
    </pre>
  )
}

function Deliveries({ deliveries }: { deliveries: Delivery[] }) {
  if (deliveries.length === 0) {
    return <p className="text-sm text-slate-500">No destinations configured.</p>
  }

  return (
    <ul className="space-y-2 text-xs">
      {deliveries.map((delivery) => (
        <li key={delivery.id} className="font-mono">
          <span className="font-semibold">{delivery.state}</span>
          <span className="text-slate-500">
            {' '}
            · {delivery.attempts} attempt{delivery.attempts === 1 ? '' : 's'}
            {delivery.last_status_code !== null && ` · HTTP ${delivery.last_status_code}`}
          </span>
          {delivery.last_error && (
            <p className="mt-1 font-sans text-slate-600 dark:text-slate-400">
              {delivery.last_error}
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}

export function RequestDetail({ viewToken, id }: { viewToken: string; id: number }) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [deliveries, setDeliveries] = useState<Delivery[]>([])
  const [error, setError] = useState<string | null>(null)

  // Mounted with a key, so a different capture is a different component: the
  // state already starts empty and there is nothing to reset here.
  useEffect(() => {
    let live = true

    Promise.all([api.getRequest(viewToken, id), api.listDeliveries(viewToken, id)])
      .then(([loaded, sent]) => {
        if (!live) return
        setDetail(loaded)
        setDeliveries(sent)
      })
      .catch(() => live && setError('Could not load this capture.'))

    return () => {
      live = false
    }
  }, [viewToken, id])

  if (error) return <p className="p-4 text-sm text-red-600">{error}</p>
  if (!detail) return <p className="p-4 text-sm text-slate-500">Loading…</p>

  return (
    <div className="pb-8">
      <header className="px-4 py-3">
        <h2 className="font-mono text-sm font-semibold">
          {detail.method} {detail.path || '/'}
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          #{detail.id} · {new Date(detail.received_at).toLocaleString()}
          {detail.source_ip && ` · from ${detail.source_ip}`}
          {detail.duration_ms !== null && ` · ${detail.duration_ms} ms`}
        </p>
      </header>

      {detail.signature && (
        <Section title="Signature">
          <p className="text-sm font-medium">
            {detail.signature.valid ? 'Valid' : 'Not valid'} · {detail.signature.provider}
          </p>
          {/* The sentence that says what to do next: the whole reason for the feature. */}
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {detail.signature.detail}
          </p>
        </Section>
      )}

      {detail.body_truncated && (
        <Section title="Truncated">
          <p className="text-sm text-amber-700 dark:text-amber-400">
            This body hit the size limit and was cut short, so a signature over it can never
            match.
          </p>
        </Section>
      )}

      <Section title="Body">
        <Body detail={detail} />
        <a
          className="mt-2 inline-block text-xs text-sky-700 underline dark:text-sky-400"
          href={api.bodyUrl(viewToken, detail.id)}
        >
          Download the raw bytes
        </a>
      </Section>

      <Section title="Headers">
        <Pairs pairs={detail.headers} />
      </Section>

      {detail.query && (
        <Section title="Query">
          <Pairs pairs={detail.query} />
        </Section>
      )}

      <Section title="Deliveries">
        <Deliveries deliveries={deliveries} />
      </Section>
    </div>
  )
}
