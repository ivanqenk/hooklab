# Hooklab — web interface

React 19 + TypeScript + Tailwind 4 on Vite. Talks to the backend on port 8010 through the dev
server's proxy, so the browser only ever sees one origin.

```bash
npm install
npm run dev        # http://localhost:5173 — needs the backend running on 8010
npm run build      # tsc -b across the three projects, then Vite
npm test           # vitest
npx oxlint src     # linter (oxlint, not eslint: it is the Vite 8 default)
```

## What is worth knowing before changing this

**Stored XSS is the risk that matters.** Every view renders content written by whoever sent the
webhook, and the page's URL carries the view token — so a script running here could read that token
and every payload behind it. `src/xss.test.ts` walks the source and fails on
`dangerouslySetInnerHTML`, `innerHTML`, `srcdoc`, on any `href` not built by `api.`, and on any use
of `localStorage`, `sessionStorage` or `document.cookie`. Those rules are not stylistic. If an HTML
preview is ever wanted, it goes in a sandboxed iframe without `allow-same-origin`.

**The view token is in the URL fragment**, which is never sent to a server. That keeps it out of
access logs and proxies. It is still in the address bar and in history, which is why `index.html`
carries `<meta name="referrer" content="no-referrer">`.

**`useEventStream` is where the difficulty lives**, and each point in its header comment is a bug
that only appears under conditions a quick manual test does not reproduce: StrictMode's double
mount, render batching under burst traffic, the sliding window, and opening history and the live
feed together rather than in sequence.

**`EventSource`, not `fetch` streaming — for now.** It reconnects and resends `Last-Event-ID` for
free, which is most of what this hook would otherwise implement, but it cannot send custom headers.
The day the view token moves into an `Authorization` header, this becomes `fetch` + `ReadableStream`
with the resume handled by hand. A deliberate trade, not an oversight.

**The signing secret is write-only.** The backend encrypts it and returns it from no route, so there
is nothing to load back into the form and no way to show it. Replacing it is the only edit.

**Tests compile as their own TypeScript project.** The app's config exposes only `vite/client` on
purpose: application code runs in a browser and has no business reaching `node:fs`. Widening it so
one test would compile hands every component an API it must not use.

## Layout

| Path | Contents |
|---|---|
| `src/api/` | Types mirroring the backend schemas, and the HTTP client. |
| `src/hooks/useEventStream.ts` | History plus the live SSE feed, merged and batched. |
| `src/components/` | The endpoint view, the list, the detail, and the settings panel. |
| `src/xss.test.ts` | The standing guard on the rendering rules above. |
