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

## Push notifications

| Method | Path | Body / returns |
|---|---|---|
| GET | `/api/push/key` | `{public_key, subscribers}`. `public_key` is the VAPID `applicationServerKey` (base64url). |
| POST | `/api/push/subscribe` | a `PushSubscription` JSON → `{subscribers}`. 400 unless the endpoint is a browser push service (or in `HERDRWEB_PUSH_HOSTS`). |
| POST | `/api/push/unsubscribe` | `{"endpoint"}` → `{subscribers}` |
| POST | `/api/push/test` | sends a test notification to every subscriber → `{sent, dropped, failed}` (needs writes on) |

Push payload (JSON, encrypted per RFC 8291):
`{"title","body","pane_id","blocked":<count of blocked panes>}`.
