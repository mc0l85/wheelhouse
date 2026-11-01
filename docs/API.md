# API reference

Base path `/api`. There is no authentication (see docs/NETWORKING.md). Mutating
calls and WebSocket handshakes that carry a browser `Origin` header must be
same-origin, or the server answers **403** (WebSocket: closed before accept).
Requests for a host not in `HERDRWEB_ALLOWED_HOSTS` get **400**. Writes (and the
test push) return **403** unless `HERDRWEB_ENABLE_WRITES=1`. A malformed pane ID
or option-like key returns **400**. herdr failures return **502**.
Interactive docs: `/docs` (OpenAPI).

## WebSockets

| Path | Purpose |
|---|---|
| `/api/ws/terminal?rows=&cols=` | A real herdr client in a PTY. Server → client: binary PTY bytes. Client → server: binary or text keystrokes (writes only); JSON `{"type":"resize","rows","cols"}`, `{"type":"ping"}` (→ `{"type":"pong"}`), `{"type":"input","data"}`. |
| `/api/ws/events` | herdr's event stream: `{"type":"stream","up":bool}`, then `{"type":"event","event","pane_id",…}` frames (agent status, scroll, structure). |

## Read

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{ok, server_running, session, pane_count, writes_enabled, …}` |
| GET | `/api/tree` | workspace → tab → pane tree in herdr's order; blocked panes carry `blocked_since` |
| GET | `/api/panes`, `/api/panes/{id}` | panes |
| GET | `/api/agents`, `/api/tabs`, `/api/worktrees`, `/api/workspaces` | herdr lists |
| GET | `/api/layout/{pane_id}` | the pane's tab layout (rects, zoom) |
| GET | `/api/panes/{id}/read?source=visible\|recent&lines=&format=text\|ansi` | pane content |

## Write (needs `HERDRWEB_ENABLE_WRITES=1`)

`POST /api/panes/{id}/send-text` `{"text"}` · `send-keys` `{"keys":["Enter"]}` ·
`run` `{"command"}` · `split` · `rename` · `focus` · `zoom` `{"mode":"toggle"}` · `close`

## Image upload (needs `HERDRWEB_ENABLE_WRITES=1`)

`POST /api/upload/image` with the raw image as the body (PNG, JPEG, GIF or WebP,
checked from the bytes; up to `HERDRWEB_UPLOAD_MAX_MB`, default 20) →
`{path, bytes, type}`. The file lands in `<HERDRWEB_STATE_DIR>/uploads/`; files
older than 7 days, or past the newest 200, are pruned on the next upload. **415**
for anything else, **413** when too large. The page pastes `path` into the pane.

## Push notifications

| Method | Path | Body / returns |
|---|---|---|
| GET | `/api/push/key` | `{public_key, subscribers}`. `public_key` is the VAPID `applicationServerKey` (base64url). |
| POST | `/api/push/subscribe` | a `PushSubscription` JSON → `{subscribers}`. 400 unless the endpoint is a browser push service (or in `HERDRWEB_PUSH_HOSTS`). |
| POST | `/api/push/unsubscribe` | `{"endpoint"}` → `{subscribers}` |
| POST | `/api/push/test` | `{"delay": 0-30}` (optional). No delay: sends a test notification to every subscriber → `{sent, dropped, failed, id}`. With a delay: answers at once with `{scheduled, delay}` and sends later, so the phone can be locked first (needs writes on) |
| POST | `/api/push/receipt` | `{"id", "shown", "error"}`, posted by the service worker once a notification is on screen (or could not be shown) |
| GET | `/api/push/receipts` | `{receipts: [{id, shown, error, at, after_s}]}`: the last 20, in memory only |

Push payload (JSON, encrypted per RFC 8291):
`{"title","body","pane_id","blocked":<count of blocked panes>,"id":<receipt id>}`.
