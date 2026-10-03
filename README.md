# Hermes-A2A

> **One-line inter-agent tasks over [A2A](https://a2a-protocol.org) for [Hermes Agent](https://nousresearch.com).**
> Reply formatting · audit-log lookup · an approval gate before any task that looks like a memory share, credential exchange, or persona edit.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Version v0.1.0](https://img.shields.io/badge/version-v0.1.0-blue.svg)](https://github.com/Ex8-ca/Hermes-A2A/releases/tag/v0.1.0) [![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)

**Released 2026-10-03 · [CHANGELOG](CHANGELOG.md) · [Report an issue](https://github.com/Ex8-ca/Hermes-A2A/issues)**

**Install this version:**

```bash
hermes plugins install https://github.com/Ex8-ca/Hermes-A2A.git@v0.1.0
hermes plugins enable a2a_bridge
```

---

## What this is

Hermes-A2A is a plugin for [Hermes Agent](https://github.com/just-every/hermes-agent) that wraps the bundled **A2A platform plugin** (`hermes-agent/plugins/platforms/a2a/`) with a friendlier tool surface and a safety gate. Once installed, your agent can talk to any other A2A-compliant agent — including other Hermes instances, LangChain, CrewAI, Google ADK, OpenClaw — with a single `a2a_bridge_send()` call. It speaks the **A2A v1.0** open protocol (Linux Foundation standard), not a vendor-locked dialect.

## Disclosure

| | |
|---|---|
| **Engine** | Wraps the bundled Hermes A2A platform plugin. No external runtime, no `npx`, no first-launch network fetch. Pure stdlib Python, in-process. |
| **Required peer** | The bundled Hermes A2A platform plugin must be enabled: `hermes plugins enable a2a`. The bridge is a UX layer on top of it. |
| **Required Python** | 3.10+ (tested on 3.10, 3.11, 3.12 in CI). |
| **Platforms** | Linux, macOS, Windows — wherever Hermes Agent runs. No platform-specific code in this plugin. |
| **Network** | None added by this plugin. All networking goes through the underlying A2A platform plugin (auth + redaction + rate limit already configured there). |
| **Filesystem** | Read-only access to `~/.hermes/a2a_audit.jsonl` and `~/.hermes/a2a_conversations/<context_id>.jsonl` — files Hermes already manages. No writes outside the plugin's own directory. |
| **Credentials** | None required by this plugin. Per-peer bearer tokens (if any) live in the user's `~/.hermes/.env` and are read by the A2A platform plugin, not by us. **No credential ever travels in the A2A payload** — the approval gate refuses such tasks. |
| **What this plugin does NOT do** | Open its own sockets · spawn subprocesses · write to your config or memory files · invoke your tools without your say-so · carry credentials in payload · auto-share memory with peers. |
| **License** | MIT. This plugin wraps the Hermes A2A platform plugin (MIT, Nous Research); both licenses are preserved. |

## What you get

Five new tools for your agent, in the `a2a_bridge` toolset:

| Tool | What it does |
|---|---|
| `a2a_bridge_send` | Send a task to a peer. Sensitive content triggers an **approval block** instead of firing. |
| `a2a_bridge_confirm` | After the user agrees, fire the call for real. |
| `a2a_bridge_audit` | Recent audit entries for a peer, formatted as a markdown table. |
| `a2a_bridge_list_peers` | Distinct peers seen in the audit log, most recent first. |
| `a2a_bridge_history` | Recall a prior A2A conversation by `context_id`. |

## Approval gate

Tasks whose message looks sensitive are **stopped before they go on the wire**. The agent sees a structured approval block and the user has to confirm with `a2a_bridge_confirm(approved: true)`.

| Category | Triggered by |
|---|---|
| `memory_share` | `memory`, `remember`, `MEMORY.md`, `SOUL.md`, `persona`, `daily logs`, `heartbeat` |
| `credential_share` | `api key`, `bearer token`, `password`, `secret`, and known token shapes (`sk-…`, `ghp_…`, `xox*-?`, `AIza…`) |
| `config_write` | `write to config.yaml`, `update your persona`, `set A2A_*` / `HERMES_*` env vars |

Bias is **toward asking** — false positives just trigger an extra confirmation; false negatives can leak secrets silently. See [`plugins/a2a_bridge/approval.py`](plugins/a2a_bridge/approval.py) for the full regex set.

## Design notes

- **Empty audit tables and missing peer lists are explicit designed states, not errors.** `a2a_bridge_audit()` returns `(no matching audit entries)` when nothing matches the filter, and `a2a_bridge_list_peers()` returns `(no peers seen yet — make a call first)` when the audit log is empty or has never been written. If you see one of those markers, the plugin is telling you it has nothing to show — not that the log is corrupt or unreadable.
- **The plugin does not open its own network sockets.** It goes through your existing A2A platform plugin, which already handles auth, redaction, rate-limit, audit logging, and conversation persistence.
- **No blanket "sync everything" memory button.** Per-slice consent only; the two-party memory-share protocol is documented in [`plugins/a2a_bridge/references/memory-share.md`](plugins/a2a_bridge/references/memory-share.md).
- **No token-in-payload send.** Even if you ask the plugin to send an API key to a peer, the credential-share gate intercepts it. Use a secret manager, not the A2A channel.

## Install

You need:

- A working **Hermes Agent** install with the A2A platform plugin enabled.
- Python 3.10+.
- At least one A2A peer configured in `~/.hermes/config.yaml` under `a2a_agents:` (or reachable by URL).

### Option A — `hermes plugins install` from this repo (recommended)

```bash
hermes plugins install https://github.com/Ex8-ca/Hermes-A2A.git@v0.1.0
hermes plugins enable a2a_bridge
```

Pins the version. To upgrade later, install a newer tag and `hermes plugins enable a2a_bridge` again.

### Option B — `pip install` from the Git tag

```bash
pip install "hermes-a2a-bridge @ git+https://github.com/Ex8-ca/Hermes-A2A.git@v0.1.0"
# Then symlink (or copy) the installed plugin tree into your Hermes plugins dir:
ln -s "$(python -c 'import os, plugins.a2a_bridge; print(os.path.dirname(plugins.a2a_bridge.__file__))')" \
      ~/.hermes/hermes-agent/plugins/a2a_bridge
hermes plugins enable a2a_bridge
```

### Option C — manual drop-in (no pip)

```bash
git clone https://github.com/Ex8-ca/Hermes-A2A.git
git -C Hermes-A2A checkout v0.1.0
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
git -C Hermes-A2A checkout v0.1.0
cd Hermes-A2A
python -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest plugins/a2a_bridge/tests/ -v
```

54 unit tests cover the approval classifier, the audit-log reader, and the tool handlers (with a stub PluginContext — no live Hermes required). The 9 live-peer integration tests are **skipped by default** unless `HERMES_A2A_TEST_TOKEN` is set; see `plugins/a2a_bridge/tests/integration_smoke.py` for details.

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
├── CHANGELOG.md                       # release notes
├── .github/workflows/ci.yml           # GitHub Actions CI
├── .github/workflows/release.yml      # release artifact build
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
│       ├── test_tools.py
│       └── integration_smoke.py       # 9 live-peer tests, opt-in via HERMES_A2A_TEST_TOKEN
```

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built on top of the [Hermes Agent](https://nousresearch.com) A2A platform plugin (MIT). The A2A protocol is a Linux Foundation open standard. Plugin design cues (the "disclosure-in-the-lede" README pattern, the pinned-version catalog model) come from the [`hermes-plugin-kiwi`](https://hermes-agent.nousresearch.com/docs/plugins/kiwi) reference plugin (Apache-2.0) and other community plugins in the [Hermes plugin catalog](https://hermes-agent.nousresearch.com/docs/plugins/).