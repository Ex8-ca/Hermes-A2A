# Hermes-A2A

> **One-line inter-agent tasks over [A2A](https://a2a-protocol.org) for [Hermes Agent](https://nousresearch.com).**
> Reply formatting · audit-log lookup · an approval gate before any task that looks like a memory share, credential exchange, or persona edit.

Hermes-A2A is a plugin for [Hermes Agent](https://github.com/just-every/hermes-agent) (or a compatible fork) that wraps the bundled **A2A platform plugin** with a friendlier tool surface and a safety gate. Once installed, your agent can talk to any other A2A-compliant agent — including other Hermes instances, LangChain, CrewAI, Google ADK, OpenClaw — with a single `a2a_bridge_send()` call.

It speaks the **A2A v1.0** open protocol (Linux Foundation standard), not a vendor-locked dialect.

---

## What you get

Five new tools for your agent, in the `a2a_bridge` toolset:

| Tool | What it does |
|---|---|
| `a2a_bridge_send` | Send a task to a peer. Sensitive content triggers an **approval block** instead of firing. |
| `a2a_bridge_confirm` | After the user agrees, fire the call for real. |
| `a2a_bridge_audit` | Recent audit entries for a peer, formatted as a markdown table. |
| `a2a_bridge_list_peers` | Distinct peers seen in the audit log, most recent first. |
| `a2a_bridge_history` | Recall a prior A2A conversation by `context_id`. |

The plugin **does not** open its own network sockets. It goes through your existing A2A platform plugin, which already handles auth, redaction, rate-limit, audit logging, and conversation persistence.

## Approval gate

Tasks whose message looks sensitive are **stopped before they go on the wire**. The agent sees a structured approval block and the user has to confirm with `a2a_bridge_confirm(approved: true)`.

| Category | Triggered by |
|---|---|
| `memory_share` | `memory`, `remember`, `MEMORY.md`, `SOUL.md`, `persona`, `daily logs`, `heartbeat` |
| `credential_share` | `api key`, `bearer token`, `password`, `secret`, and known token shapes (`sk-...`, `ghp_...`, `xox*-?`, `AIza...`) |
| `config_write` | `write to config.yaml`, `update your persona`, `set A2A_*` / `HERMES_*` env vars |

Bias is **toward asking** — false positives just trigger an extra confirmation; false negatives can leak secrets silently. See [`plugins/a2a_bridge/approval.py`](plugins/a2a_bridge/approval.py) for the full regex set.

## Install

You need:

- A working **Hermes Agent** install with the A2A platform plugin enabled.
- Python 3.10+.
- At least one A2A peer configured in `~/.hermes/config.yaml` under `a2a_agents:` (or reachable by URL).

### Option A — install from GitHub (recommended)

```bash
pip install git+https://github.com/Ex8-ca/Hermes-A2A.git
```

That puts the plugin tree on your Python path. To activate it in your Hermes install, copy (or symlink) the wheel's `plugins/a2a_bridge/` tree into `~/.hermes/hermes-agent/plugins/`:

```bash
# Find where pip put it
python -c "import plugins.a2a_bridge; print(plugins.a2a_bridge.__file__)"

# Then symlink or copy that directory to your Hermes plugins dir
ln -s "$(python -c 'import os, plugins.a2a_bridge; print(os.path.dirname(plugins.a2a_bridge.__file__))')" \
      ~/.hermes/hermes-agent/plugins/a2a_bridge
```

### Option B — `hermes plugins install` from a packed archive

```bash
git clone https://github.com/Ex8-ca/Hermes-A2A.git
cd Hermes-A2A
hermes plugins pack plugins/a2a_bridge
# produces hermes-a2a-bridge.hermes-plugin or similar
hermes plugins install hermes-a2a-bridge.hermes-plugin
hermes plugins enable a2a_bridge
```

### Option C — manual drop-in

```bash
git clone https://github.com/Ex8-ca/Hermes-A2A.git
cp -r Hermes-A2A/plugins/a2a_bridge ~/.hermes/hermes-agent/plugins/
hermes plugins enable a2a_bridge
```

### Verify

```bash
hermes plugins doctor ~/.hermes/hermes-agent/plugins/a2a_bridge
# expected: manifest: a2a-bridge 0.1.0 (standalone)
#           OK: runtime discovery, manifest parsing, import, and registration passed
#           registrations: 5 tool(s), 0 hook(s)
```

Then start a fresh session and try:

```
a2a_bridge_list_peers()
a2a_bridge_audit(last=5)
a2a_bridge_send(agent="my-peer", message="Reply with PONG")
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

## Running tests

```bash
git clone https://github.com/Ex8-ca/Hermes-A2A.git
cd Hermes-A2A
python -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest plugins/a2a_bridge/tests/ -v
```

54 tests cover the approval classifier, the audit-log reader, and the tool handlers (with a stub PluginContext — no live Hermes required).

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

### Option 2 — certbot + nginx

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

## Project layout

```
Hermes-A2A/
├── README.md                          # this file
├── LICENSE                            # MIT
├── pyproject.toml                     # pip-installable wheel config
├── .github/workflows/ci.yml           # GitHub Actions CI
├── plugins/a2a_bridge/                # the plugin itself
│   ├── __init__.py                    #   register(ctx) — wires 5 tools + stashes ctx
│   ├── approval.py                    #   sensitive-task classifier (with self-test)
│   ├── audit.py                       #   audit-log reader + table formatter
│   ├── tools.py                       #   JSON schemas + handlers + dispatch
│   ├── plugin.yaml                    #   manifest
│   ├── README.md                      #   plugin-level README
│   ├── references/
│   │   ├── tls-setup.md               #     caddy + caddyfile recipe
│   │   └── memory-share.md            #     two-party consent protocol
│   └── tests/
│       ├── conftest.py
│       ├── test_approval.py
│       ├── test_audit.py
│       └── test_tools.py
└── plugins/a2a_bridge/tests/integration_smoke.py
                                      # 9 live-peer tests (run against a real A2A server)
```

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built on top of the [Hermes Agent](https://nousresearch.com) A2A platform plugin (MIT). The A2A protocol is a Linux Foundation open standard.