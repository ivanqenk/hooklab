/**
 * The HTTP side of the API. The live feed is separate -- see `useEventStream`.
 *
 * Every call goes to a relative path. The dev server proxies those to the
 * backend, so the browser only ever sees one origin: no CORS in development, and
 * the same shape as production behind a single reverse proxy.
 */

import type {
  Delivery,
  EndpointCreated,
  EndpointPublic,
  RequestDetail,
  RequestPage,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })

  if (!response.ok) {
    // The backend answers 404 for an unknown token rather than 403, so that a
    // valid token cannot be told apart from an invented one. The message here
    // has to respect that and stay just as uninformative.
    const detail = await response.text()
    throw new ApiError(response.status, detail || response.statusText)
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
}
