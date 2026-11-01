# Reaching Wheelhouse from your phone, safely

Wheelhouse has **no login**. Whoever can reach it can type into your panes, which
is a shell as your user. So the network is the lock. Pick one option below and
**never** forward its port on your router.

Phones also need **HTTPS** with a certificate they trust. Safari only allows the
microphone, the clipboard API, the service worker and push notifications on a
secure origin (`localhost` is the only exception).

| Option | Reachable from | HTTPS | Effort |
|---|---|---|---|
| A1. Tailscale | your devices, anywhere | Tailscale Serve gives you a cert | low |
| A2. NetBird (or ZeroTier / WireGuard) | your devices, anywhere | via your reverse proxy (C) | low–medium |
| B. Twingate (or Cloudflare WARP + private network) | your devices, anywhere | via your reverse proxy (C) | low–medium |
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

## A1. Tailscale (simplest)

1. Install Tailscale on the herdr machine and on the phone; sign both into the
   same tailnet.
2. On the herdr machine, with Wheelhouse on `127.0.0.1:8086`:

   ```bash
   tailscale serve --bg 8086
   ```

   That serves `https://<machine>.<tailnet>.ts.net` with a real certificate, to
   your tailnet only.
3. Add `<machine>.<tailnet>.ts.net` to `HERDRWEB_ALLOWED_HOSTS` and restart.
4. On the phone, open that URL in Safari, then Share › Add to Home Screen.

**Do not** use `tailscale funnel`; it publishes to the internet.

## A2. NetBird

1. Install NetBird on the herdr machine and on the phone, and run `netbird up`
   on the machine (or use a setup key). Both peers join your NetBird network.
2. Give the phone a policy (Access Control) that allows it to reach the herdr
   machine on port 443 (or whichever port your proxy listens on).
3. NetBird does not issue certificates, so put the reverse proxy from option C
   in front: point a hostname you own at the machine's NetBird address (or its
   LAN address, if the phone routes there through NetBird) and use the DNS
   challenge for the certificate.
4. Add that hostname to `HERDRWEB_ALLOWED_HOSTS`.

ZeroTier and plain WireGuard work the same way: the VPN gets the phone onto the
network, and option C supplies HTTPS.

## B. Twingate

Twingate needs no inbound ports and no public DNS, and the phone just runs the
Twingate app.

1. In the Twingate admin console, add a Remote Network and deploy a Connector
   inside your network (Docker, systemd package or a small VM).
2. Set up the reverse proxy from option C, with a hostname you own. Its DNS
   record can point at a private address; only the Connector needs to resolve it.
3. Add a Resource for that hostname (port 443) and give access to a group that
   contains only you.
4. Add the hostname to `HERDRWEB_ALLOWED_HOSTS`.
5. On the phone: install Twingate, sign in, connect, open the hostname, then
   Add to Home Screen.

Cloudflare WARP with a private network route works the same way.

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

1. Run `cloudflared` on the herdr machine or anywhere on your network.
2. Zero Trust › Networks › Tunnels: add a public hostname
   `wheelhouse.example.com` that routes to `http://localhost:8086` (or
   `http://<herdr-machine>:8086` if `cloudflared` runs elsewhere; then bind
   Wheelhouse to that address).
3. Zero Trust › Access › Applications: add a self-hosted app for that hostname
   with a policy that allows only your identity (Google, GitHub, one-time PIN).
   A longer session duration means the home-screen app asks you to sign in less
   often.
4. **Add the tunnel hostname to `HERDRWEB_ALLOWED_HOSTS`.** The tunnel talks to
   Wheelhouse directly, not through your LAN proxy, so it arrives with its own
   `Host`. If you skip this, the phone gets 400 "Invalid host header" even
   though the LAN URL works.
5. Test from a private window: you must hit the Access login before Wheelhouse
   loads.

Push notifications still work, because they travel from your server to
Apple/Google, not through the tunnel. Each hostname is its own origin to the
browser, so turn notifications on from the URL the home-screen app actually uses.

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
- In the home-screen app: Controls › Notifications › Turn on, then **Send test (5 s)**
  and lock the phone. `GET /api/push/receipts` shows `"shown": true` once the
  notification has reached the phone's screen.
