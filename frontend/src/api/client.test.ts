import { describe, expect, it } from 'vitest'

import { errorDetail } from './client'

const FALLBACK = 'Unprocessable Content'

describe('errorDetail', () => {
  it('unwraps the sentence FastAPI puts in `detail`', () => {
    // Verbatim from the running backend. These sentences are the product: the
    // SSRF check exists to say *which* rule an address broke, and showing the
    // JSON around it reads as a crash rather than an explanation.
    const body = JSON.stringify({
      detail:
        "'localhost' resolves to 127.0.0.1, which is a loopback address. " +
        'Destinations have to be reachable from the public internet.',
    })

    expect(errorDetail(body, FALLBACK)).toBe(
      "'localhost' resolves to 127.0.0.1, which is a loopback address. " +
        'Destinations have to be reachable from the public internet.',
    )
  })

  it('falls back rather than spilling a pydantic validation list', () => {
    // A 422 whose detail is a list means we sent the wrong shape -- our bug, not
    // the user's. Rendering it puts `[{"type":"missing","loc":["body",...` on
    // screen, which tells the user nothing and looks like a crash.
    const body = JSON.stringify({ detail: [{ type: 'missing', loc: ['body', 'secret'] }] })

    expect(errorDetail(body, FALLBACK)).toBe(FALLBACK)
  })

  it('falls back on JSON that is not an error envelope', () => {
    expect(errorDetail(JSON.stringify({ message: 'nope' }), FALLBACK)).toBe(FALLBACK)
  })

  it('keeps a plain-text body, which is all a proxy error page offers', () => {
    // A 502 from a reverse proxy never reaches FastAPI, so there is no envelope
    // to unwrap and the body itself is the only clue available.
    expect(errorDetail('502 Bad Gateway', FALLBACK)).toBe('502 Bad Gateway')
  })

  it('falls back on an empty body', () => {
    expect(errorDetail('', FALLBACK)).toBe(FALLBACK)
    expect(errorDetail('   ', FALLBACK)).toBe(FALLBACK)
  })

  it('falls back on an empty detail rather than showing nothing at all', () => {
    expect(errorDetail(JSON.stringify({ detail: '  ' }), FALLBACK)).toBe(FALLBACK)
  })
})
