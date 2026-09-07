import type { SignatureReason, SignatureSummary } from '../api/types'

/**
 * Not every failure means the same thing, and colouring them all red would throw
 * away the diagnosis the backend worked to produce.
 *
 * A truncated body or an unreadable stored secret are *our* problem, not a bad
 * signature -- showing them as failures would send someone hunting a secret that
 * was never wrong. They get their own neutral treatment.
 */
const TONE: Record<SignatureReason, string> = {
  valid: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200',
  mismatch: 'bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200',
  timestamp_out_of_tolerance:
    'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
  malformed_header: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
  missing_header: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  no_secret: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  unreadable_secret: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  body_truncated: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
}

const LABEL: Record<SignatureReason, string> = {
  valid: 'signed',
  mismatch: 'bad signature',
  timestamp_out_of_tolerance: 'stale event',
  malformed_header: 'bad header',
  missing_header: 'unsigned',
  no_secret: 'no secret',
  unreadable_secret: 'secret unreadable',
  body_truncated: 'body truncated',
}

export function SignatureBadge({ signature }: { signature: SignatureSummary | null }) {
  if (!signature) return null

  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${TONE[signature.reason]}`}
      title={`${signature.provider}: ${signature.reason}`}
    >
      {LABEL[signature.reason]}
    </span>
  )
}
