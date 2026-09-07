/**
 * The two things that turn a capture log into a gateway: verifying signatures
 * and forwarding on.
 *
 * Kept in an overlay rather than a third column. The list and the detail are
 * what someone stares at while debugging, and settings are touched once per
 * endpoint — giving them permanent screen space would cost the working view
 * width it uses constantly to serve a panel used almost never.
 */

import { useEffect, useRef } from 'react'

import type { EndpointPublic } from '../api/types'
import { DestinationSettings } from './DestinationSettings'
import { SignatureSettings } from './SignatureSettings'

interface Props {
  viewToken: string
  endpoint: EndpointPublic
  onEndpointChanged: () => void
  onClose: () => void
}

export function SettingsPanel({ viewToken, endpoint, onEndpointChanged, onClose }: Props) {
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Escape closes. Bound on the document rather than the panel so it works
    // wherever focus happens to be, including inside a text field.
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    panel.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-10 flex items-start justify-center bg-slate-900/40 p-4 sm:p-10"
      // A click that starts and ends on the backdrop closes. Checking the target
      // is what keeps a drag that ends outside a text selection from counting.
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Endpoint settings"
        tabIndex={-1}
        className="max-h-full w-full max-w-2xl overflow-y-auto rounded-lg bg-white shadow-xl outline-none dark:bg-slate-950"
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 className="text-sm font-semibold">Endpoint settings</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            className="rounded px-2 py-1 text-sm text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            Close
          </button>
        </header>

        <SignatureSettings
          viewToken={viewToken}
          endpoint={endpoint}
          onChanged={onEndpointChanged}
        />
        <DestinationSettings viewToken={viewToken} />
      </div>
    </div>
  )
}
