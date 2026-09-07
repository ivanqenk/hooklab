import type { RequestSummary } from '../api/types'

/** How many captures stay in memory. Older ones are a page fetch away. */
export const WINDOW_SIZE = 500

/**
 * Fold arriving captures into the list: newest first, one entry per id, bounded.
 *
 * Its own module so it can be tested without React in the way. Three properties
 * matter, and each one is a bug if it is missing:
 *
 * - **Deduplication by id.** History and the live stream are opened together, so
 *   an overlap between them is expected rather than exceptional.
 * - **Sorted by id, not by arrival.** Under concurrency the backend's stream
 *   order does not follow id order -- an id is assigned before commit and
 *   published after -- so appending in arrival order shows captures out of
 *   sequence.
 * - **Bounded.** Ten thousand captures cannot live in React state.
 */
export function merge(
  existing: RequestSummary[],
  arriving: RequestSummary[],
): RequestSummary[] {
  if (arriving.length === 0) return existing

  const byId = new Map<number, RequestSummary>()
  for (const item of existing) byId.set(item.id, item)
  for (const item of arriving) byId.set(item.id, item)

  return [...byId.values()].sort((a, b) => b.id - a.id).slice(0, WINDOW_SIZE)
}
