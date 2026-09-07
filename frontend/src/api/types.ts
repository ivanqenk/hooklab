/**
 * Mirrors of the backend's response schemas.
 *
 * Hand-written rather than generated from the OpenAPI document. The API is small
 * and stable, and a generator would be another build step to keep working for
 * about thirty lines of types. If the surface grows, generating them becomes the
 * right call -- these drifting silently is the real risk.
 */

export type SignatureReason =
  | 'valid'
  | 'no_secret'
  | 'unreadable_secret'
  | 'missing_header'
  | 'malformed_header'
  | 'body_truncated'
  | 'timestamp_out_of_tolerance'
  | 'mismatch'

export interface SignatureSummary {
  provider: string
  valid: boolean
  reason: SignatureReason
}

export interface SignatureDetail extends SignatureSummary {
  /** The sentence that says what to do next. The whole point of the feature. */
  detail: string
  checked_at: string
}

export interface RequestSummary {
  id: number
  method: string
  path: string
  content_type: string | null
  body_size: number
  body_truncated: boolean
  source_ip: string | null
  received_at: string
  duration_ms: number | null
  signature: SignatureSummary | null
}

export interface RequestDetail extends RequestSummary {
  query: Record<string, string> | null
  headers: Record<string, string>
  body_json: unknown
  /**
   * A body is arbitrary bytes and JSON cannot carry those. Exactly one of
   * `body_text` and `body_base64` is ever set; `body_encoding` says which.
   */
  body_text: string | null
  body_base64: string | null
  body_encoding: string | null
  signature: SignatureDetail | null
}

export interface RequestPage {
  items: RequestSummary[]
  next_cursor: number | null
}

export interface EndpointCreated {
  id: string
  name: string | null
  ingest_url: string
  ingest_token: string
  /** The only response that ever carries this. Losing it means losing access. */
  view_token: string
  created_at: string
  expires_at: string
}

export interface EndpointPublic {
  id: string
  name: string | null
  ingest_url: string
  created_at: string
  expires_at: string
  request_count: number
  signature_provider: string | null
}

export type DeliveryState = 'pending' | 'delivered' | 'failed' | 'exhausted'

export interface Delivery {
  id: number
  request_id: number
  destination_id: string
  state: DeliveryState
  attempts: number
  next_attempt_at: string | null
  last_status_code: number | null
  last_error: string | null
  idempotency_key: string
  created_at: string
  delivered_at: string | null
}
