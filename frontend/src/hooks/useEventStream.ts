/**
 * The live list of captures: history from the API, new arrivals over SSE.
 *
 * Four things here are less obvious than they look, and each one is a bug that
 * only shows up under conditions a quick manual test does not reproduce.
 *
 * **1. StrictMode mounts effects twice in development.** React deliberately
 * mounts, unmounts and remounts to expose missing cleanup. Without closing the
 * `EventSource` on the way out you end up with two live connections and every
 * capture appearing twice -- in development only, which sends people hunting for
 * a duplication bug in the backend that does not exist.
 *
 * **2. Renders are batched.** One `setState` per arriving event is fine at three
 * captures a minute and pathological at three hundred a second, which is exactly
 * when someone is watching. Events land in a ref and are flushed on a timer, so
 * a burst costs one render instead of hundreds.
 *
 * **3. The list is a sliding window.** Ten thousand captures cannot live in React
 * state. Only the most recent are kept in memory; older ones stay reachable
 * through the cursor pagination the API already provides.
 *
 * **4. History and the live feed are opened together, not in sequence.** Fetching
 * the existing page first and subscribing afterwards leaves a window whose
 * events are lost silently. Both start at once and the results are merged by id,
 * so an overlap costs a duplicate that is filtered rather than a hole that is
 * not.
 *
 * `EventSource` rather than `fetch` streaming, for now. It reconnects and resends
 * `Last-Event-ID` on its own, which is most of what this hook would otherwise
 * have to implement -- but it cannot send custom headers. The day the view token
 * moves out of the URL into an Authorization header, this has to become
 * `fetch` + `ReadableStream` with the resume handled by hand. A deliberate
 * trade, not an oversight.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { RequestSummary } from '../api/types'
import { merge } from './merge'

/** How long arriving events sit in the buffer before one render is spent. */
const FLUSH_MS = 100

export type StreamStatus = 'connecting' | 'live' | 'error'

interface StreamState {
  requests: RequestSummary[]
  status: StreamStatus
  /** Set when the server said the client fell too far behind to be caught up. */
  missedTooMuch: boolean
}

export function useEventStream(viewToken: string): StreamState & {
  reload: () => void
} {
  const [requests, setRequests] = useState<RequestSummary[]>([])
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const [missedTooMuch, setMissedTooMuch] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const buffer = useRef<RequestSummary[]>([])

  const reload = useCallback(() => {
    setMissedTooMuch(false)
    setReloadKey((n) => n + 1)
  }, [])

  useEffect(() => {
    // Guards the async history fetch: after cleanup runs, its result must not be
    // written into state belonging to a connection that is already gone.
    let live = true
    buffer.current = []

    const flush = window.setInterval(() => {
      if (buffer.current.length === 0) return
      const arriving = buffer.current
      buffer.current = []
      setRequests((current) => merge(current, arriving))
    }, FLUSH_MS)

    const source = new EventSource(
      `/api/endpoints/${encodeURIComponent(viewToken)}/stream`,
    )

    source.addEventListener('ready', () => {
      if (live) setStatus('live')
    })

    source.addEventListener('request', (event) => {
      buffer.current.push(JSON.parse((event as MessageEvent<string>).data))
    })

    source.addEventListener('gap', () => {
      // Too far behind to be caught up event by event. The honest answer is to
      // say so and let the list be reloaded, rather than quietly showing a hole.
      if (live) setMissedTooMuch(true)
    })

    source.onerror = () => {
      // EventSource reconnects by itself, so this is "not connected right now"
      // rather than a failure. Only a closed source is terminal.
      if (live) setStatus(source.readyState === EventSource.CLOSED ? 'error' : 'connecting')
    }

    // Started alongside the stream, not after it: see note 4 above.
    api
      .listRequests(viewToken)
      .then((page) => {
        if (live) setRequests((current) => merge(current, page.items))
      })
      .catch(() => {
        if (live) setStatus('error')
      })

    return () => {
      live = false
      window.clearInterval(flush)
      // The cleanup StrictMode exists to check for.
      source.close()
    }
  }, [viewToken, reloadKey])

  return { requests, status, missedTooMuch, reload }
}
