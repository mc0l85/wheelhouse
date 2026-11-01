# Architecture

```
 Browser / home-screen app (React 18 + xterm.js 5.5)
   ├─ xterm.js  ◀── WS /api/ws/terminal (binary PTY bytes) ──┐
   ├─ pane selector, inbox  ◀── WS /api/ws/events + REST ────┤
   ├─ 🎤 dictation, inbox replies ── REST send-text/keys ────┤
   └─ service worker (sw.js)  ◀── Web Push (Apple/Google/Mozilla) ◀─┐
                                                              │      │
 FastAPI backend (backend/app)                                ▼      │
   ├─ routers/terminal  pty.fork → `herdr --session <name>`  (a real herdr client)
   ├─ herdr.py          herdr CLI (JSON) + control socket for focus/zoom/lists
   ├─ events.py         one events.subscribe stream, fanned out to browsers
   ├─ push.py           VAPID key, subscriptions, "agent blocked" pushes ──┘
   ├─ routers/upload    pasted images → <state dir>/uploads, path pasted into the pane
   └─ origin.py         same-origin gate for WebSockets and mutating calls
```

## The terminal: a real herdr client in a PTY

`WS /api/ws/terminal` forks a PTY and execs `herdr --session <HERDR_SESSION>`,
stripping `HERDR_*` env so herdr doesn't refuse to nest. PTY output goes to the
browser as binary frames. Browser keystrokes go back to the PTY only when
`HERDRWEB_ENABLE_WRITES=1`. JSON text frames are control messages: `resize`
(→ `TIOCSWINSZ`), `ping` (→ `pong`, the client heartbeat) and `input`.

Because it is a genuine herdr client, the browser shows exactly what a terminal
shows. herdr has one size per session, so the most recent client to attach or
resize sets the geometry for all clients.

xterm.js keeps no scrollback (`scrollback: 0`). Scrolling, on a mouse wheel or a
one-finger drag turned into wheel events, goes to herdr, which scrolls the pane
under the pointer. Mouse reporting is passed through, so herdr's own drag-select
works, and its OSC 52 copy lands in the browser clipboard.

## Structured reads and pane-targeted writes

`herdr.py` shells out to the herdr CLI (JSON envelopes) and uses the control
socket directly for verbs the CLI lacks (`pane.focus`, `pane.zoom`, list calls).
Every mutation checks the same writes flag (CLI verbs through `_write()`, socket
verbs and terminal keystrokes inline). Pane IDs are validated before they reach
an argv, since herdr's CLI has no `--` separator. The inbox and dictation send
text and keys **by pane ID** (`send-text`, `send-keys`), so they never depend
on which pane has focus.

## Events and push

`events.py` keeps one `events.subscribe` connection to herdr (global structural
events plus per-pane agent status and scroll), resubscribing when the pane set
changes. Browsers get a thin "something changed" frame and refetch `/api/tree`.
Polling is the fallback while the stream is down.

`push.py` hooks the same stream. When a pane's agent status becomes `blocked`,
it waits 2 seconds and re-reads the pane. herdr replays recent history on
subscribe, and agents flicker, so only a pane that is **still** blocked gets a
push: one per blocked episode. Panes already blocked when the stream starts are
never announced. Subscriptions that the push service reports as gone (404/410)
are deleted.

## Security

No login, by design (see docs/NETWORKING.md). The defenses that remain:

- **Bind address:** `127.0.0.1` by default.
- **Host allowlist:** requests (WebSockets included) for a `Host` not in
  `HERDRWEB_ALLOWED_HOSTS` get 400. This stops DNS rebinding, where a hostile page
  points its own name at your machine so its requests look same-origin.
- **Writes off by default:** a fresh install is read-only.
- **Origin gate:** WebSocket handshakes and mutating `/api` calls that carry a
  browser `Origin` must match the request's host (or `HERDRWEB_ALLOWED_ORIGINS`).
  This blocks cross-site WebSocket hijacking and CSRF from any page you visit.
- **Fixed session:** the session is the server's setting, never the client's.
- **Push endpoints:** only the browsers' push services (Apple, Google, Mozilla,
  Microsoft) are accepted, so a client can't make the server POST to an internal
  URL. Subscriptions are capped; the test push needs writes on.
- **Private state:** the VAPID key and subscriptions are written mode 0600 in a
  0700 directory.
- **Terminal limit:** at most `HERDRWEB_MAX_TERMINALS` concurrent terminal sockets.

Known limit: resize frames are accepted in read-only mode too, and herdr has one
size per session, so a read-only viewer can still change everyone's geometry.
