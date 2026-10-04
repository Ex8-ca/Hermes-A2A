# Hermes-A2A

> **A2A inter-agent tasks for [Hermes Agent](https://nousresearch.com) — with a directory, a discovery protocol, and a tailnet-friendly error UX.**
> Reply formatting · audit-log lookup · an approval gate before any task that looks like a memory share, credential exchange, or persona edit · public directory at `hermes-a2a.dpmob.com`.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Version v0.5.2](https://img.shields.io/badge/version-v0.5.2-blue.svg)](https://github.com/Ex8-ca/Hermes-A2A/releases/tag/v0.5.2) [![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml) [![Tests 286](https://img.shields.io/badge/tests-286-blue.svg)](directory/README.md)

[**Live directory**](https://hermes-a2a.dpmob.com) · [CHANGELOG](CHANGELOG.md) · [ROADMAP](ROADMAP.md) · [Report an issue](https://github.com/Ex8-ca/Hermes-A2A/issues) · Topics: `hermes-a2a`, `a2a`, `agent-to-agent`, `hermes-plugin`, `tailscale`

---

## What this is

Two things:

1. **A plugin** for [Hermes Agent](https://github.com/just-every/hermes-agent) that wraps the bundled **A2A platform plugin** (`hermes-agent/plugins/platforms/a2a/`) with a friendlier tool surface and a safety gate. Once installed, your agent can talk to any other A2A-compliant agent — including other Hermes instances, LangChain, CrewAI, Google ADK, OpenClaw — with a single `a2a_bridge_send()` call. It speaks the **A2A v1.0** open protocol (Linux Foundation standard), not a vendor-locked dialect.

2. **A directory** at [hermes-a2a.dpmob.com](https://hermes-a2a.dpmob.com) where agents can be discovered by URL or by the tailnet they live on. The directory is a public catalog — anyone can browse it; Tailscale MagicDNS names show up with `transport: "tailscale-magicdns"` so a discoverer knows exactly what network they need to be on to reach each entry.

Both halves share the same release. The plugin lives in `plugins/a2a_bridge/`; the directory is `directory/`. They communicate via Cloudflare KV (namespace `AGENTS`) — submissions through the plugin's operator tooling (`directory/operator/`) write to KV; the directory reads from KV.

## What's new

| Version | Highlights |
|---|---|
| **v0.5.2** | Per-tailnet discovery: `/list?tailnet=<name>`, `/list?reachable_via=<transport>`. Cross-tailnet catalog: anyone sees entries from any tailnet. |
| **v0.4.3** | **Peer-unreachable error UX.** A Tailscale MagicDNS URL on a network failure now says "this peer is on a Tailscale tailnet you may not be on" instead of the generic urllib error. |
| **v0.4.2** | `render_agents.py --prune` to keep build-time artifacts in sync with the live KV. |
| **v0.4.1** | Directory schema: `transport` field on every entry (`tailscale-magicdns`, `lan`, `https`, `http-public`), `/list?transport=` filter, transport chip on per-agent SSR pages. |
| **v0.4.0** | Tailscale integration: `discover_tailscale.py`, `make_submission_tailscale.py`. The directory learns about Tailscale CGNAT (`100.64/10`) and `.ts.net` MagicDNS. |
| **v0.3.0** | Replay protection (5-minute window). Real memory-slice fetching. `from_public_key` cross-check against meeting records. `policy_rotate.py` for the operator allowlist. |
| **v0.2.0** | ed25519 identity crypto. `agent_id = sha256(pubkey)[:16]` base32 (Nostr-style). Path-traversal blocked in `handle_receive_public.write_to`. Approval-gate coverage extended. |

Full release notes: [CHANGELOG.md](CHANGELOG.md). Roadmap and what's planned next: [ROADMAP.md](ROADMAP.md).

## Quick start

### For a third-party discoverer (no Hermes required)

You don't need to install anything to look at the directory. From any machine with Python 3.10+:

```bash
git clone https://github.com/Ex8-ca/Hermes-A2A.git
cd Hermes-A2A
python3 scripts/discovery_client_demo.py
```

The demo shows you exactly what a third party sees: the catalog, the per-agent SSR pages, the tailnet and transport filters, and the v0.4.3 contextual error when calling a Tailscale MagicDNS agent from a non-tailnet host. Run it now:

```
[1/5] GET /list (unfiltered)
  ✓ HTTP 200, 2 entries in the catalog
      • ai5080  [tailscale-magicdns]  http://ai5080.taila6e2e.ts.net:9900/.well-known/agent-card.json
      • desktop_2  [tailscale-magicdns]  http://minisforum-desktop.taila6e2e.ts.net:9900/.well-known/agent-card.json
[2/5] GET /list?tailnet=taila6e2e
  ✓ HTTP 200, 2 entries on taila6e2e
[3/5] GET /list?reachable_via=https
  ✓ HTTP 200, 0 entries reachable via public HTTPS
[4/5] GET /agent/desktop_2.html (per-agent SSR page)
  ✓ HTTP 200, 5242 bytes
[5/5] Try to call a MagicDNS agent from a non-tailnet caller
  ✓ network error: URLError: [Errno -2] Name or service not known
  "Error: peer is on a Tailscale tailnet (URL: ...). Your host is not on that
   tailnet, so the A2A call could not reach it. Run `tailscale status` to
   check your tailnet membership, or ask the peer operator to add your host
   to the tailnet's ACL. ..."
```

### For an agent operator (you publish your agent)

Submit your agent to the directory via the operator tooling:

```bash
# Option A — auto-discover your Tailscale node
python3 directory/operator/make_submission_tailscale.py \
    --agent-id desktop_2 \
    --name "Desktop" \
    --description "Hermes on the office desktop"

# Option B — manual URL
python3 directory/operator/make_submission.py \
    --agent-id desktop_2 \
    --name "Desktop" \
    --agent-card-url http://192.168.1.2:9900/.well-known/agent-card.json
```

Both sign the submission envelope with `~/.hermes/directory_operator.key` (ed25519). The directory validates the signature against the operator allowlist before storing the entry. To rotate the allowlist (multi-operator support), use `policy_rotate.py`.

### For an agent that needs to talk to peers (you install the plugin)

```bash
hermes plugins install https://github.com/Ex8-ca/Hermes-A2A.git@v0.5.2
hermes plugins enable a2a_bridge
```

Five new tools show up under the `a2a_bridge` toolset:

| Tool | What it does |
|---|---|
| `a2a_bridge_send` | Send a task to a peer. Sensitive content triggers an **approval block** instead of firing. |
| `a2a_bridge_confirm` | After the user agrees, fire the call for real. |
| `a2a_bridge_audit` | Recent audit entries for a peer, formatted as a markdown table. |
| `a2a_bridge_list_peers` | Distinct peers seen in the audit log, most recent first. |
| `a2a_bridge_history` | Recall a prior A2A conversation by `context_id`. |

Then in a fresh session:

```
a2a_bridge_list_peers()
a2a_bridge_audit(last=5)
a2a_bridge_send(agent="ai5080", message="Reply with PONG")
```

## The directory

[hermes-a2a.dpmob.com](https://hermes-a2a.dpmob.com) is a public catalog of agents that speak A2A. Anyone can read `/list`; submissions require an operator key.

**Three endpoints:**

- `GET /list` — JSON catalog. Supports orthogonal filters: `?transport=` (alias `?reachable_via=`), `?tailnet=`. The response shape: `{version, count, agents, filters}`.
- `GET /agent/<id>.html` — pre-rendered static page per agent (transport chip, declared capabilities, declared_at, last_verified, operator signature — all baked in at build time so the page is complete with JavaScript disabled).
- `POST /submit` — operator-signed submission envelope. Ed25519 signature against the operator allowlist. Auto-fills `transport` from `agent_card_url` via `classifyTransport()` (RFC1918 + loopback + link-local + Tailscale CGNAT + `.ts.net` MagicDNS). Optional manual override.

**Filter examples:**

```bash
# All entries
curl https://hermes-a2a.dpmob.com/list

# All MagicDNS entries across all tailnets
curl 'https://hermes-a2a.dpmob.com/list?tailnet=.ts.net'

# Entries on a specific tailnet
curl 'https://hermes-a2a.dpmob.com/list?tailnet=taila6e2e'

# Entries reachable via public HTTPS (none in our catalog)
curl 'https://hermes-a2a.dpmob.com/list?reachable_via=https'
```

Full directory docs: [directory/README.md](directory/README.md).

## The peer-unreachable error UX (v0.4.3)

When the underlying A2A platform's call to a peer times out or fails with a network error, the bridge plugin (v0.4.3+) replaces the generic urllib error with a contextual message naming the specific network requirement:

- **Tailscale MagicDNS URL** → `Error: peer is on a Tailscale tailnet (URL: ...). Your host is not on that tailnet, so the A2A call could not reach it. Run \`tailscale status\` to check your tailnet membership, or ask the peer operator to add your host to the tailnet's ACL.`
- **LAN/RFC1918 URL** → `Error: peer is on a private network (URL: ...). Your host is not on the same network, so the A2A call could not reach it. Confirm your machine is on the same LAN, VPN, or Tailscale tailnet as the peer.`
- **Public URL** → unchanged (the platform's generic error is fine; the URL itself is the problem).
- **Auth error (HTTP 401/403 / "rejected auth")** → unchanged (different fix path).

The classification helper is in [`plugins/a2a_bridge/tools.py`](plugins/a2a_bridge/tools.py) (`_classify_peer_unreachable`, `_is_private_or_loopback_url`). 9 unit tests in [`tests/test_peer_unreachable.py`](plugins/a2a_bridge/tests/test_peer_unreachable.py).

## Approval gate

Tasks whose message looks sensitive are **stopped before they go on the wire**. The agent sees a structured approval block and the user has to confirm with `a2a_bridge_confirm(approved: true)`.

| Category | Triggered by |
|---|---|
| `memory_share` | `memory`, `remember`, `MEMORY.md`, `SOUL.md`, `persona`, `daily logs`, `heartbeat` |
| `credential_share` | `api key`, `bearer token`, `password`, `secret`, and known token shapes (`sk-…`, `ghp_…`, `xox*-?`, `AIza…`) |
| `config_write` | `write to config.yaml`, `update your persona`, `set A2A_*` / `HERMES_*` env vars |

Bias is **toward asking** — false positives just trigger an extra confirmation; false negatives can leak secrets silently. See [`plugins/a2a_bridge/approval.py`](plugins/a2a_bridge/approval.py) for the full regex set.

## Disclosure

| | |
|---|---|
| **Engine** | Wraps the bundled Hermes A2A platform plugin. No external runtime, no `npx`, no first-launch network fetch. Pure stdlib Python, in-process. |
| **Required peer** | The bundled Hermes A2A platform plugin must be enabled: `hermes plugins enable a2a`. The bridge is a UX layer on top of it. |
| **Required Python** | 3.10+ (tested on 3.10, 3.11, 3.12, 3.13 in CI). |
| **Auto-start at boot** | Optional. Run `./scripts/install-gateway-service.sh --start` after install to set up a systemd user service that starts `hermes gateway run` (and binds A2A) on every boot. See [Auto-start at boot](#auto-start-at-boot). |
| **Platforms** | Linux, macOS, Windows — wherever Hermes Agent runs. No platform-specific code in this plugin. |
| **Network** | None added by this plugin. All networking goes through the underlying A2A platform plugin (auth + redaction + rate limit already configured there). |
| **Filesystem** | Read-only access to `~/.hermes/a2a_audit.jsonl` and `~/.hermes/a2a_conversations/<context_id>.jsonl` — files Hermes already manages. No writes outside the plugin's own directory. |
| **Credentials** | None required by this plugin. Per-peer bearer tokens (if any) live in the user's `~/.hermes/.env` and are read by the A2A platform plugin, not by us. **No credential ever travels in the A2A payload** — the approval gate refuses such tasks. |
| **What this plugin does NOT do** | Open its own sockets · spawn subprocesses · write to your config or memory files · invoke your tools without your say-so · carry credentials in payload · auto-share memory with peers. |
| **License** | MIT. This plugin wraps the Hermes A2A platform plugin (MIT, Nous Research); both licenses are preserved. |

## Install (full instructions)

You need:

- A working **Hermes Agent** install with the A2A platform plugin enabled.
- Python 3.10+.
- At least one A2A peer configured in `~/.hermes/config.yaml` under `a2a_agents:` (or reachable by URL).

### Option A — `hermes plugins install` from this repo (recommended)

```bash
hermes plugins install https://github.com/Ex8-ca/Hermes-A2A.git@v0.5.2
hermes plugins enable a2a_bridge
```

Pins the version. To upgrade later, install a newer tag and `hermes plugins enable a2a_bridge` again.

### Option B — `pip install` from the Git tag

```bash
pip install "hermes-a2a-bridge @ git+https://github.com/Ex8-ca/Hermes-A2A.git@v0.5.2"
# Then symlink (or copy) the installed plugin tree into your Hermes plugins dir:
ln -s "$(python -c 'import os, plugins.a2a_bridge; print(os.path.dirname(plugins.a2a_bridge.__file__))')" \
      ~/.hermes/hermes-agent/plugins/a2a_bridge
hermes plugins enable a2a_bridge
```

### Option C — manual drop-in (no pip)

```bash
git clone https://github.com/Ex8-ca/Hermes-A2A.git
git -C Hermes-A2A checkout v0.5.2
cp -r Hermes-A2A/plugins/a2a_bridge ~/.hermes/hermes-agent/plugins/
hermes plugins enable a2a_bridge
```

### Verify

```bash
hermes plugins doctor ~/.hermes/hermes-agent/plugins/a2a_bridge
# expected: manifest: a2a-bridge 0.5.2 (standalone)
#           OK: runtime discovery, manifest parsing, import, and registration passed
#           registrations: 5 tool(s), 0 hook(s)
```

## Configuring a peer

The plugin reads peers from `~/.hermes/config.yaml`:

```yaml
a2a_agents:
  my-peer:
    url: "https://my-peer.example.com"
    auth:
      type: bearer
      token: "${A2A_PEER_TOKEN_my_peer}"   # resolved from ~/.hermes/.env
    timeout: 120
    capabilities: [a2a_call, browser, terminal, skills]
```

The peer must be a real A2A v1.0-compliant agent serving an Agent Card at `/.well-known/agent-card.json`.

If you'd rather discover peers via the public directory than hand-configure them, fetch `https://hermes-a2a.dpmob.com/list?tailnet=<yourtailnet>` and write the entries to your config. The `scripts/discovery_client_demo.py` script shows the protocol.

## Running tests

```bash
git clone https://github.com/Ex8-ca/Hermes-A2A.git
cd Hermes-A2A
# Plugin tests (Python)
uv run --with pytest --with cryptography --with pyyaml \
    python -m pytest plugins/a2a_bridge/tests/ -v

# Directory tests (Node)
cd directory
node --test tests/*.test.js

# Operator tooling tests (Python)
cd ..
uv run --with pytest --with cryptography --with pyyaml \
    python -m pytest directory/operator/tests/ -v
```

**286 tests total at v0.5.2:** 198 plugin unit + 62 directory Node + 25 directory Python + 1 skipped plugin e2e (opt-in via `HERMES_A2A_TEST_TOKEN`).

The live end-to-end probe is `scripts/e2e_live_test.py` (10 checks across LAN round-trip, Tailscale round-trip, directory filters, per-agent SSR, Tailscale discovery, integration smoke). It requires the operator's two test gateways to be running on the network.

## Auto-start at boot

By default, the A2A platform only runs while you have an active session of `hermes` or `hermes gateway run` going. To make A2A serve on every boot — so a peer on another machine can reach your agent at 2am — install the bundled systemd user service:

```bash
cd /path/to/Hermes-A2A
./scripts/install-gateway-service.sh --start
```

This:
1. Writes `~/.config/systemd/user/hermes-a2a-gateway.service` running `hermes gateway run`
2. Enables it (starts on next login/boot)
3. Enables `loginctl enable-linger` for your user so the service survives logout
4. Starts it now

Verify it worked:

```bash
systemctl --user status hermes-a2a-gateway.service
journalctl --user -u hermes-a2a-gateway.service -f
```

The service uses the **same Python interpreter the Hermes desktop uses** (under `~/.hermes/tools/python-3.14.7+202****0901-linux-x64/`) so it has the same deps (`ruamel.yaml`, `cryptography`, `httpx`, etc.) loaded. No virtualenv setup needed.

To uninstall:

```bash
./scripts/install-gateway-service.sh --uninstall
```

> **Why a user service, not a system service?** A user service runs in your login session with your env (`HERMES_HOME`, the right Python, the right dotenv) and doesn't need root to install. That's the right scope for a per-user agent.

> **Conflict with the desktop.** The Hermes desktop auto-spawns a `serve` process that does **not** load the A2A platform. If the desktop respawns `serve` after the systemd service starts `gateway run`, both will run, but only the systemd one binds port 9900. The two are designed to coexist; if you see them fighting, stop the desktop-spawned one with `systemctl --user stop hermes-desktop-backend` (or just close the desktop) — the systemd service will continue running.

## TLS for the public internet

The plugin speaks plain HTTP to configured peers. To put your agent on the public internet (so other agents on different networks can reach it), put it behind a TLS reverse proxy and advertise the public URL on your Agent Card.

You have two real options:

### Option 1 — Caddy (recommended)

Caddy auto-requests a Let's Encrypt cert and renews it for you. The full recipe is in [`plugins/a2a_bridge/references/tls-setup.md`](plugins/a2a_bridge/references/tls-setup.md). Short version:

```bash
sudo apt install caddy          # or pacman -S caddy
# /etc/caddy/Caddyfile:
#   a2a.example.com {
#     reverse_proxy 127.0.0.1:9900
#     timeouts { read 10m  write 10m }
#   }
sudo systemctl reload caddy
```

Then in `~/.hermes/.env`:

```env
A2A_HOST=127.0.0.1              # keep bind on localhost; Caddy is the public face
A2A_PORT=9900
A2A_PUBLIC_URL=https://a2a.example.com
A2A_BEARER_TOKEN=...            # or A2A_PEER_TOKENS for per-peer creds
```

Restart the gateway: `hermes gateway restart`.

### Option 2 — Tailscale Funnel (cross-tailnet access)

If you're on a Tailscale tailnet (or want to be), you can expose port 9900 via [Tailscale Funnel](https://tailscale.com/kb/1223/funnel/) to get a public HTTPS endpoint with Tailscale-managed certs:

```bash
sudo tailscale set --accept-routes
tailscale funnel 9900 on
```

The directory learns the entry's URL via `agent_card_url`; the URL gets classified as `tailscale-magicdns` if it's a `*.ts.net` host, or as a public HTTPS endpoint if you also use Caddy in front. Both work.

### Option 3 — certbot + nginx

Same idea, more manual steps. `certbot --nginx -d a2a.example.com` handles the cert + nginx vhost. Same `A2A_HOST`/`A2A_PORT`/`A2A_PUBLIC_URL` config.

**You don't need Caddy specifically — you need a TLS reverse proxy. Caddy is just the easiest one. Let's Encrypt is the certificate authority either way.**

## Two-party memory exchange

Sharing memory between two agents is a sensitive action. The plugin ships a documented protocol in [`plugins/a2a_bridge/references/memory-share.md`](plugins/a2a_bridge/references/memory-share.md) that requires explicit consent on **both** sides:

1. Sender's approval gate catches the request; user confirms.
2. Receiver's prompt-injection filter treats the inbound text as untrusted — it previews the diff and waits for the receiver's user to approve.
3. The receiver's own `MEMORY.md` is updated with an attributed, dated entry (source peer + task_id).
4. Both audit logs record the transfer.

Nothing is auto-written. Either side can refuse.

## What's intentionally NOT here

- **No direct outbound HTTP from the plugin.** It always goes through the underlying A2A platform plugin's audited path.
- **No blanket "sync everything" memory button.** Per-slice consent only.
- **No token-in-payload send.** Even if you ask the plugin to send an API key to a peer, the credential-share gate intercepts it. Use a secret manager, not the A2A channel.
- **No anonymous public mode.** A2A without auth is `127.0.0.1`-only by default; widening past loopback requires a token plus a trusted-peer allow-list.
- **No transport verification on submit (yet).** v0.5 trusts the `transport` field inferred by `classifyTransport()`. A future version (v0.6) will let submitters attach a Tailscale API key to prove the claim.

## Project layout

```
Hermes-A2A/
├── README.md                                  # this file
├── LICENSE                                    # MIT
├── pyproject.toml                             # pip-installable wheel config (name: hermes-a2a-bridge)
├── CHANGELOG.md                               # release notes
├── ROADMAP.md                                 # what's planned + test-counts table
├── .github/workflows/ci.yml                   # GitHub Actions CI
├── .github/workflows/release.yml              # release artifact build
├── plugins/a2a_bridge/                        # the plugin itself (Python)
│   ├── __init__.py                            #   register(ctx) — wires 5 tools + stashes ctx
│   ├── approval.py                            #   sensitive-task classifier (with self-test)
│   ├── audit.py                               #   audit-log reader + table formatter
│   ├── canonical.py                           #   canonical JSON for signing
│   ├── handshake.py                           #   envelope build/parse + replay-window check
│   ├── identity.py                            #   ed25519 key generation + agent_id derivation
│   ├── keyring.py                             #   load + persist operator/meeting keys
│   ├── meetings.py                            #   persistent meeting record store
│   ├── slice.py                               #   memory slice fetch + heading-anchor split
│   ├── tools.py                               #   JSON schemas + handlers + dispatch
│   │                                          #   + _classify_peer_unreachable (v0.4.3)
│   ├── plugin.yaml                            #   manifest
│   ├── README.md                              #   plugin-level README
│   ├── references/
│   │   ├── tls-setup.md                       #     caddy + caddyfile recipe
│   │   └── memory-share.md                    #     two-party consent protocol
│   └── tests/
│       ├── conftest.py
│       ├── test_approval.py
│       ├── test_audit.py
│       ├── test_handshake.py                  #   v0.3.0 replay-window tests
│       ├── test_keyring.py
│       ├── test_meetings.py
│       ├── test_peer_unreachable.py           #   v0.4.3 contextual error tests (9)
│       ├── test_slice.py                      #   v0.3.0 fetch_contents tests
│       ├── test_tools.py
│       └── integration_smoke.py               #   9 live-peer tests, opt-in via HERMES_A2A_TEST_TOKEN
├── scripts/
│   ├── install-gateway-service.sh             # systemd user service installer
│   ├── e2e_live_test.py                       # 10-check end-to-end probe (LAN + TS + dir)
│   └── discovery_client_demo.py                # public third-party discovery demo
├── directory/                                 # the Cloudflare Pages directory
│   ├── README.md                              #   full directory docs
│   ├── pages/                                 #   Pages Functions + static assets
│   │   ├── index.html
│   │   ├── style.css
│   │   ├── agent/                             #     per-agent SSR pages + _template.html
│   │   └── functions/
│   │       ├── submit.js                      #       signed-submission handler
│   │       ├── list.js                        #       catalog + ?transport / ?tailnet filters
│   │       ├── delete.js                      #       signed-deletion handler
│   │       ├── agent/[id].js                  #       per-agent SSR function (404 fallback)
│   │       ├── canonicalize.js
│   │       └── _validate.js                   #       classifyTransport + isPrivateOrLoopbackHost
│   ├── tests/                                 # 62 Node tests (vitest/node:test)
│   │   ├── submit.test.js
│   │   ├── list.test.js
│   │   ├── validate.test.js
│   │   ├── render-agents.test.js
│   │   ├── delete.test.js
│   │   ├── multi-operator.test.js
│   │   └── self-signed-updates.test.js
│   ├── render.mjs                             # Node ESM build-time SSR
│   ├── render_one.mjs                         # single-entry SSR (used by render_agents.py)
│   ├── render_agents.py                       # Python wrapper + --prune flag (v0.4.2)
│   └── operator/                              # operator tooling (Python)
│       ├── make_submission.py                  #   submit a single agent
│       ├── make_submission_tailscale.py       #   submit using Tailscale-discovered IP
│       ├── discover_tailscale.py              #   Tailscale API device discovery
│       ├── delete_entry.py                    #   signed-deletion CLI
│       ├── policy_rotate.py                   #   operator allowlist rotation (v0.3.0)
│       └── tests/
│           ├── test_policy_rotate.py
│           ├── test_discover_tailscale.py
│           └── test_make_submission_tailscale.py
```

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built on top of the [Hermes Agent](https://nousresearch.com) A2A platform plugin (MIT). The A2A protocol is a Linux Foundation open standard. Plugin design cues (the "disclosure-in-the-lede" README pattern, the pinned-version catalog model) come from the [`hermes-plugin-kiwi`](https://hermes-agent.nousresearch.com/docs/plugins/kiwi) reference plugin (Apache-2.0) and other community plugins in the [Hermes plugin catalog](https://hermes-agent.nousresearch.com/docs/plugins/). The directory is hosted on [Cloudflare Pages](https://pages.cloudflare.com) at `hermes-a2a.dpmob.com`.