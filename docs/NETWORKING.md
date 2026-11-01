# Reaching Wheelhouse from your phone, safely

Wheelhouse has **no login**. Whoever can reach it can type into your panes, which
is a shell as your user. So the network is the lock. Pick one option below and
**never** forward its port on your router.

Phones also need **HTTPS** with a certificate they trust. Safari only allows the
microphone, the clipboard API, the service worker and push notifications on a
secure origin (`localhost` is the only exception).

| Option | Reachable from | HTTPS | Effort |
|---|---|---|---|
| A. Mesh VPN (Tailscale / NetBird / ZeroTier / WireGuard) | your devices, anywhere | Tailscale Serve gives you a cert; others via proxy | low |
| B. Zero-trust connector (Twingate, Cloudflare WARP + private network) | your devices, anywhere | via your reverse proxy | low–medium |
| C. Reverse proxy on your LAN | home network (plus A/B for away) | Let's Encrypt DNS challenge | medium |
| D. Public tunnel + identity gate (Cloudflare Tunnel + Access) | the internet, behind SSO | Cloudflare edge | medium |

Keep `HERDRWEB_HOST=127.0.0.1` whenever a proxy or tunnel runs on the same
machine. Bind to a LAN or VPN address only when the proxy is on another host.

Whatever you choose, add the host name (or address) your phone will type to
`HERDRWEB_ALLOWED_HOSTS`. Requests for other host names are refused with 400
"Invalid host header", which is what stops DNS-rebinding attacks:

```bash
HERDRWEB_ALLOWED_HOSTS=localhost,127.0.0.1,wheelhouse.example.com
```

## A. Tailscale (simplest)

```bash
# on the herdr machine, with Wheelhouse on 127.0.0.1:8086
tailscale serve --bg 8086
```

That serves `https://<machine>.<tailnet>.ts.net` with a real certificate, to your
tailnet only (add that name to `HERDRWEB_ALLOWED_HOSTS`). Install Tailscale on the phone and open that URL. **Do not** use
`tailscale funnel`; it publishes to the internet.

NetBird, ZeroTier and plain WireGuard work the same way: bind Wheelhouse to the
VPN interface address, or proxy to it, and use option C's reverse proxy for HTTPS.

## B. Twingate (or another zero-trust connector)

Run the connector on your network, add a Resource for the hostname of your
reverse proxy (option C), and grant it to your account. The phone runs the
Twingate client. Nothing is exposed publicly, and DNS can stay private.

## C. Reverse proxy with TLS (Caddy example)

Point a hostname you own at the proxy (a private IP is fine) and get a
certificate with the **DNS challenge**, which needs no inbound ports:

```caddy
# needs a Caddy build with the DNS provider plugin (e.g. caddy-dns/cloudflare)
wheelhouse.example.com {
    tls {
        dns cloudflare {env.CF_API_TOKEN}
    }
    reverse_proxy 127.0.0.1:8086
}
```

Caddy forwards WebSockets automatically. With nginx or Nginx Proxy Manager, turn
on WebSocket support (`proxy_http_version 1.1`, `Upgrade`/`Connection` headers),
pass the `Host` header through unchanged (the Origin check compares against it),
and raise `proxy_read_timeout` (for example to 1 hour) so idle terminals stay open.

## D. Cloudflare Tunnel **with Access** (only if you need it public)

A tunnel alone puts a shell on the internet. Always pair it with Access:

1. Zero Trust › Networks › Tunnels: route `wheelhouse.example.com` to
   `http://localhost:8086`.
2. Zero Trust › Access › Applications: add a self-hosted app for that hostname
   with a policy that allows only your identity (Google, GitHub, one-time PIN).
3. Test from a private window: you must hit the Access login before Wheelhouse loads.

Push notifications still work, because they travel from your server to
Apple/Google, not through the tunnel.

## If the terminal won't connect behind a proxy

- **400 "Invalid host header":** the name you browse to isn't in
  `HERDRWEB_ALLOWED_HOSTS`.
- **Terminal never connects, typing returns 403:** a proxy rewrote the `Host`
  header, so the browser's `Origin` looks foreign. Pass `Host` through
  unchanged, or allow the public origin explicitly:

```bash
HERDRWEB_ALLOWED_ORIGINS=https://wheelhouse.example.com
```

## Checks after setup

- From a device **outside** your VPN/LAN, the URL must fail or demand a login.
- `curl -I https://<host>/` from inside returns `200`.
- In the app: Controls › Notifications › Turn on, then **Send test**.
