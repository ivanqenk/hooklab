/**
 * The HTTP side of the API. The live feed is separate -- see `useEventStream`.
 *
 * Every call goes to a relative path. The dev server proxies those to the
 * backend, so the browser only ever sees one origin: no CORS in development, and
 * the same shape as production behind a single reverse proxy.
 */

import type {
  Delivery,
  Destination,
  EndpointCreated,
  EndpointPublic,
  RequestDetail,
  RequestPage,
  SignatureProvider,
  VerificationResult,
} from './types'

export class ApiError extends Error {
  // Assigned in the body rather than declared as a constructor parameter
  // property: `erasableSyntaxOnly` is on, so every bit of syntax has to vanish
  // when the types are stripped, and parameter properties emit real code.
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Pull the human sentence out of a failed response.
 *
 * FastAPI wraps its errors as `{"detail": ...}`, so showing the raw body would
 * put `{"detail":"An endpoint may have at most 10 destinations."}` on screen.
 * That matters more than it looks: the messages behind these errors -- why an
 * address was rejected, why a provider is unknown -- are written to be read by
 * the person who has to fix it, and JSON punctuation around them reads as a
 * crash instead of an explanation.
 *
 * Validation errors arrive as a list of objects instead of a string. Those are
 * our bug, not the user's, so they collapse to the status text rather than
 * spilling a pydantic trace onto the page.
 */
export function errorDetail(body: string, fallback: string): string {
  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    // Not JSON at all -- a proxy's error page, say. The body is the best
    // message on offer, and an empty one leaves only the status.
    return body.trim() || fallback
  }

  if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
    const { detail } = parsed as { detail: unknown }
    if (typeof detail === 'string' && detail.trim()) return detail
  }
  // JSON, but not a sentence: a pydantic validation list, or some other shape.
  // Those describe our bug rather than the user's, so they stay off the screen.
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })

  if (!response.ok) {
    // The backend answers 404 for an unknown token rather than 403, so that a
    // valid token cannot be told apart from an invented one. The message here
    // has to respect that and stay just as uninformative.
    throw new ApiError(response.status, errorDetail(await response.text(), response.statusText))
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  createEndpoint: (name?: string) =>
    request<EndpointCreated>('/api/endpoints', {
      method: 'POST',
      body: JSON.stringify({ name: name || null }),
    }),

  getEndpoint: (viewToken: string) =>
    request<EndpointPublic>(`/api/endpoints/${encodeURIComponent(viewToken)}`),

  listRequests: (viewToken: string, before?: number, limit = 50) => {
    const query = new URLSearchParams({ limit: String(limit) })
    if (before !== undefined) query.set('before', String(before))
    return request<RequestPage>(
      `/api/endpoints/${encodeURIComponent(viewToken)}/requests?${query}`,
    )
  },

  getRequest: (viewToken: string, id: number) =>
    request<RequestDetail>(
      `/api/endpoints/${encodeURIComponent(viewToken)}/requests/${id}`,
    ),

  /**
   * The raw body, byte for byte. Served as octet-stream with an attachment
   * disposition, so this is a link the browser downloads rather than renders --
   * which is the point: rendering attacker-chosen HTML from our origin would
   * turn the domain into malware hosting.
   */
  bodyUrl: (viewToken: string, id: number) =>
    `/api/endpoints/${encodeURIComponent(viewToken)}/requests/${id}/body`,

  listDeliveries: (viewToken: string, requestId: number) =>
    request<Delivery[]>(
      `/api/endpoints/${encodeURIComponent(viewToken)}/deliveries/by-request/${requestId}`,
    ),

  retryDelivery: (viewToken: string, deliveryId: number) =>
    request<Delivery>(
      `/api/endpoints/${encodeURIComponent(viewToken)}/deliveries/${deliveryId}/retry`,
      { method: 'POST' },
    ),

  /**
   * Turn signature verification on, or change the secret.
   *
   * Configured behind the *view* token, never the ingest one: the ingest token
   * is public by design, and if it could set the secret then anyone who saw the
   * URL could switch verification off or point it at a secret of their own.
   *
   * The secret is write-only. The backend encrypts it and returns it from no
   * route, in no form -- so there is nothing to read back into the form, and the
   * only way to change it is to send a new one.
   */
  configureSignature: (viewToken: string, provider: SignatureProvider, secret: string) =>
    request<EndpointPublic>(
      `/api/endpoints/${encodeURIComponent(viewToken)}/signature`,
      { method: 'PUT', body: JSON.stringify({ provider, secret }) },
    ),

  clearSignature: (viewToken: string) =>
    request<void>(`/api/endpoints/${encodeURIComponent(viewToken)}/signature`, {
      method: 'DELETE',
    }),

  listDestinations: (viewToken: string) =>
    request<Destination[]>(`/api/endpoints/${encodeURIComponent(viewToken)}/destinations`),

  createDestination: (viewToken: string, targetUrl: string) =>
    request<Destination>(`/api/endpoints/${encodeURIComponent(viewToken)}/destinations`, {
      method: 'POST',
      body: JSON.stringify({ target_url: targetUrl }),
    }),

  /**
   * Challenge the destination and enable forwarding if it answers correctly.
   *
   * Answers 200 with `verified: false` when the challenge fails -- not an error
   * status, because nothing about the request was wrong and the reason is the
   * part the user needs to read.
   */
  verifyDestination: (viewToken: string, destinationId: string) =>
    request<VerificationResult>(
      `/api/endpoints/${encodeURIComponent(viewToken)}/destinations/${encodeURIComponent(destinationId)}/verify`,
      { method: 'POST' },
    ),

  deleteDestination: (viewToken: string, destinationId: string) =>
    request<void>(
      `/api/endpoints/${encodeURIComponent(viewToken)}/destinations/${encodeURIComponent(destinationId)}`,
      { method: 'DELETE' },
    ),
}
