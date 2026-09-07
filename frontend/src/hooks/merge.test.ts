import { describe, expect, it } from 'vitest'

import type { RequestSummary } from '../api/types'
import { merge, WINDOW_SIZE } from './merge'

function capture(id: number): RequestSummary {
  return {
    id,
    method: 'POST',
    path: '/hooks',
    content_type: 'application/json',
    body_size: 2,
    body_truncated: false,
    source_ip: '203.0.113.1',
    received_at: '2026-09-04T12:00:00Z',
    duration_ms: 3,
    signature: null,
  }
}

const ids = (list: RequestSummary[]) => list.map((item) => item.id)

describe('merge', () => {
  it('keeps the newest first', () => {
    expect(ids(merge([], [capture(1), capture(3), capture(2)]))).toEqual([3, 2, 1])
  })

  it('sorts by id rather than by arrival order', () => {
    // The backend's stream order does not follow id order: an id is assigned
    // before the commit and published after, so under load capture 87 reaches
    // the browser ahead of 85. Appending in arrival order shows them jumbled.
    expect(ids(merge([capture(87)], [capture(85)]))).toEqual([87, 85])
  })

  it('does not duplicate an id that arrives twice', () => {
    // History and the live stream are opened together on purpose, so an overlap
    // between them is the expected case, not an exceptional one.
    expect(ids(merge([capture(2), capture(1)], [capture(2), capture(3)]))).toEqual([3, 2, 1])
  })

  it('lets a later copy of an id win', () => {
    const updated = { ...capture(1), method: 'PUT' }

    expect(merge([capture(1)], [updated])[0]?.method).toBe('PUT')
  })

  it('stays bounded no matter how much arrives', () => {
    const flood = Array.from({ length: WINDOW_SIZE * 2 }, (_, n) => capture(n + 1))

    const merged = merge([], flood)

    expect(merged).toHaveLength(WINDOW_SIZE)
    // The window keeps the newest, since those are what the user is watching.
    expect(merged[0]?.id).toBe(WINDOW_SIZE * 2)
  })

  it('returns the same list when nothing arrives', () => {
    const existing = [capture(1)]

    expect(merge(existing, [])).toBe(existing)
  })
})
