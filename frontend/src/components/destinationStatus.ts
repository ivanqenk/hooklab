/**
 * What a destination is actually doing right now, in one word plus a sentence.
 *
 * Kept apart from the component and given `now` as an argument rather than
 * reading the clock, so the interesting states are reachable from a test. The
 * circuit-breaker pause in particular only exists for a few minutes after a run
 * of failures, and that is not a window to be caught by hand.
 *
 * The order of the checks is the whole content of this function. Verification
 * comes first because it is the gate: an unverified destination forwards nothing
 * regardless of how healthy it looks, and reporting it as "paused" or "inactive"
 * would send someone to fix the wrong thing.
 */

import type { Destination } from '../api/types'

export type DestinationState = 'unverified' | 'paused' | 'inactive' | 'forwarding'

export interface DestinationStatus {
  state: DestinationState
  label: string
  detail: string
  tone: string
}

const TONE: Record<DestinationState, string> = {
  unverified: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
  paused: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
  inactive: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  forwarding: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200',
}

function status(state: DestinationState, label: string, detail: string): DestinationStatus {
  return { state, label, detail, tone: TONE[state] }
}

export function destinationStatus(destination: Destination, now: Date): DestinationStatus {
  if (!destination.verified) {
    return status(
      'unverified',
      'not verified',
      'Nothing is forwarded here yet. Prove you control this URL by echoing the token below.',
    )
  }

  // `paused_until` is left behind after the pause elapses rather than being
  // cleared, so a date in the past is the normal resting state and not a pause.
  const pausedUntil = destination.paused_until ? new Date(destination.paused_until) : null
  if (pausedUntil && pausedUntil > now) {
    return status(
      'paused',
      'paused',
      `${destination.consecutive_failures} deliveries failed in a row, so this destination is ` +
        `being spared until ${pausedUntil.toLocaleTimeString()}. It resumes on its own; retrying ` +
        `a delivery releases it immediately.`,
    )
  }

  if (!destination.active) {
    return status('inactive', 'inactive', 'Verified, but switched off. Nothing is forwarded here.')
  }

  return status('forwarding', 'forwarding', 'Every capture is forwarded here, with retries.')
}
