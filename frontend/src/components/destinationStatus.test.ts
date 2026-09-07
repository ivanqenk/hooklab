import { describe, expect, it } from 'vitest'

import type { Destination } from '../api/types'
import { destinationStatus } from './destinationStatus'

const NOW = new Date('2026-09-07T12:00:00Z')

function destination(overrides: Partial<Destination> = {}): Destination {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    target_url: 'https://example.com/hooks',
    active: true,
    verified: true,
    verification_token: 'hlv_abc',
    extra_headers: null,
    timeout_ms: 10_000,
    max_attempts: 8,
    consecutive_failures: 0,
    paused_until: null,
    created_at: '2026-09-07T11:00:00Z',
    ...overrides,
  }
}

describe('destinationStatus', () => {
  it('reports a healthy destination as forwarding', () => {
    expect(destinationStatus(destination(), NOW).state).toBe('forwarding')
  })

  it('reports an unverified destination as unverified', () => {
    expect(destinationStatus(destination({ verified: false }), NOW).state).toBe('unverified')
  })

  it('puts verification ahead of every other state', () => {
    // An unverified destination forwards nothing whatever else is true of it.
    // Calling this one "paused" would send someone to wait out a circuit breaker
    // when what is actually missing is the echo.
    const stuck = destination({
      verified: false,
      active: false,
      paused_until: '2026-09-07T12:05:00Z',
      consecutive_failures: 5,
    })

    expect(destinationStatus(stuck, NOW).state).toBe('unverified')
  })

  it('reports a live circuit-breaker pause', () => {
    const paused = destination({
      paused_until: '2026-09-07T12:05:00Z',
      consecutive_failures: 5,
    })

    expect(destinationStatus(paused, NOW).state).toBe('paused')
  })

  it('does not treat an elapsed pause as a pause', () => {
    // The backend leaves `paused_until` in place once it has passed rather than
    // clearing it, so a past date is the ordinary resting state. Comparing
    // against null instead of against the clock would leave every destination
    // that ever stumbled looking permanently paused.
    const recovered = destination({
      paused_until: '2026-09-07T11:59:00Z',
      consecutive_failures: 5,
    })

    expect(destinationStatus(recovered, NOW).state).toBe('forwarding')
  })

  it('reports a verified but switched-off destination as inactive', () => {
    expect(destinationStatus(destination({ active: false }), NOW).state).toBe('inactive')
  })

  it('always carries a sentence explaining the state', () => {
    const states = [
      destination(),
      destination({ verified: false }),
      destination({ active: false }),
      destination({ paused_until: '2026-09-07T12:05:00Z' }),
    ]

    for (const each of states) {
      expect(destinationStatus(each, NOW).detail.length).toBeGreaterThan(0)
    }
  })
})
