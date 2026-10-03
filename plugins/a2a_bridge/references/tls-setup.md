# Putting your A2A agent on the public internet (TLS)

The a2a-bridge plugin (and the underlying A2A platform plugin) speaks
plain HTTP. To run an agent that *other* agents on the internet can reach,
you need to put it behind TLS. The plugin code itself doesn't change —
the encryption lives at the transport layer.

This reference covers the simplest path: **Caddy** as a reverse proxy in
front of the A2A server, with either a real domain + Let's Encrypt, or a
self-signed cert for closed testing.

## Why TLS matters even before you "share with others"

* **Bearer tokens in plaintext** on a non-TLS link mean anyone on the
  network path can read them and impersonate your agent.
* **Inbound tasks** include user-controlled text — without TLS a network
  attacker can both eavesdrop and modify it.
* **Outbound tasks** are already redacted by the plugin for credential-
  shaped strings, but TLS is defense in depth.

## Option 1: real domain + Caddy (recommended for distribution)

If you have a hostname pointing at the box (`a2a.example.com`):

```bash
# 1. Install Caddy
sudo apt install -y caddy         # Debian / Ubuntu
sudo pacman -S caddy              # Arch / Omarchy

# 2. Write the Caddyfile (next section) and place it at /etc/caddy/Caddyfile

# 3. Tell Caddy to (re)load
sudo systemctl reload caddy
# Caddy will fetch a Let's Encrypt cert for you automatically.
```

### Minimal Caddyfile

```caddyfile
# /etc/caddy/Caddyfile
a2a.example.com {
    reverse_proxy 127.0.0.1:9900

    # The A2A plugin's adapter uses long-lived HTTP for SSE streams,
    # so we have to disable Caddy's request-and-response idle timeouts.
    timeouts {
        read  10m
        write 10m
    }
}
```

That's it. Caddy handles cert issuance + renewal + OCSP stapling.

### Update your `.env`

```env
A2A_HOST=127.0.0.1              # KEEP the bind on localhost; Caddy is the public face
A2A_PORT=9900
A2A_PUBLIC_URL=https://a2a.example.com  # what the Agent Card advertises
A2A_BEARER_TOKEN=...            # or A2A_PEER_TOKENS for per-peer creds
```

Restart the gateway (`hermes gateway restart`). Other agents will now
discover your card at `https://a2a.example.com/.well-known/agent-card.json`.

## Option 2: self-signed cert (closed testing only)

For a small group of agents behind a VPN or on a private LAN:

```bash
# Generate a self-signed cert valid for 365 days
mkdir -p ~/caddy.pem
openssl req -x509 -newkey rsa:4096 -days 365 -nodes \
  -keyout ~/caddy.pem/key.pem \
  -out    ~/caddy.pem/cert.pem \
  -subj "/CN=a2a.internal" \
  -addext "subjectAltName=DNS:a2a.internal,IP:192.168.1.3"
```

Caddyfile for self-signed:

```caddyfile
a2a.internal {
    reverse_proxy 127.0.0.1:9900
    tls /home/marc/caddy.pem/cert.pem /home/marc/caddy.pem/key.pem
}
```

Each peer agent will need to trust this cert:

```python
import ssl
ctx = ssl.create_default_context(cafile="/path/to/cert.pem")
urllib.request.urlopen(url, context=ctx)
```

Or just point Caddy at a real domain — Option 1 — and skip all of this.

## Option 3: tunnel through a TLS-aware reverse proxy you already run

If you already have nginx or traefik in front of services, just add
another virtual host that proxies `127.0.0.1:9900`. The A2A plugin
doesn't need anything beyond:

* HTTP/1.1 (no HTTP/2 required; SSE works on either)
* Read/write timeouts ≥ the longest task (`A2A_REPLY_TIMEOUT`, default 300s)
* WebSocket pass-through not required — JSON-RPC over HTTP is fine,
  SSE is used for `message/stream` and that *also* works through a
  reverse proxy as long as the proxy doesn't buffer.

## What you should NOT do

* **Expose the A2A port directly** (no reverse proxy, port-forwarded in
  your router). The plugin's prompt-injection filter and rate-limit
  help, but they're not a substitute for TLS + auth at the edge.
* **Set `A2A_HOST=0.0.0.0` AND skip `A2A_TRUSTED_PEERS`.** Without a
  trusted-peer list (or `A2A_ALLOW_ALL_USERS=true`), the dispatcher
  refuses non-loopback connections — but only when a token is set.
  Without a token, it loops back to localhost-only. Either path is fine;
  mixing them is the foot-gun.
* **Use HTTP in production peer URLs.** The plugin will dutifully send
  bearer tokens in plaintext. Don't.

## Testing that TLS is set up

```bash
# Replace with your actual hostname
curl -sS https://a2a.example.com/.well-known/agent-card.json | jq .

# Should return 200 and a JSON Agent Card. If you see a cert error,
# the proxy isn't speaking TLS to clients.
```

Then from another machine on the internet:

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"x","method":"message/send",
       "params":{"id":"probe-1","message":{"role":"user",
       "parts":[{"kind":"text","text":"Reply with PONG"}]}}}' \
  https://a2a.example.com/
```

If you get `TASK_STATE_COMPLETED` and `PONG` in the response, end-to-end
TLS + auth is working. Ship it.