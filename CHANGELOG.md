# Changelog

All notable changes to Hermes-A2A are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-10-04

### Added
- **ed25519 identity layer** — `plugins/a2a_bridge/keyring.py`. Each
  agent generates a keypair on first run, stored at
  `~/.hermes/a2a_bridge/identity.key` (mode 0600). The public key
  becomes part of the Agent Card; the agentId is now derived from
  the SHA-256 of the public key (so it survives bearer-token
  rotation). The `cryptography` library is now a runtime dep.
- **Meeting protocol** — `introduce` / `introduce_ack` JSON-RPC
  envelopes, signed end-to-end with ed25519. `Meetings` module
  persists records to `~/.hermes/a2a_bridge/meetings.json`. Each
  meeting has a 30-day default consent TTL and can be revoked
  unilaterally. Six new tools: `a2a_bridge_introduce`,
  `a2a_bridge_introduce_respond`, `a2a_bridge_meetings`,
  `a2a_bridge_revoke`, `a2a_bridge_share_public`,
  `a2a_bridge_receive_public`.
- **Public-share tools** — send and receive signed memory slices
  over the existing A2A channel. Both sides check the central
  allowlist, the meeting record, and the signature. Slices are
  appended to the receiver's MEMORY.md with a provenance comment
  capturing the source peer, public key fingerprint, and timestamps.
- **Canonical JSON** — `plugins/a2a_bridge/canonical.py` is the
  shared byte-for-byte canonicalization used by both the local
  signer and the remote verifier. Tested for parity with the
  directory Worker's verifier in `directory/worker/tools/`.
- **End-to-end demo** — `plugins/a2a_bridge/tests/e2e_demo.py`
  runs the full flow against two live agents (.2 and .3). Signs
  envelopes, persists meetings, validates signatures, appends
  provenance-tagged entries to MEMORY.md.
- **`ROADMAP.md`** — the design doc for v0.2 (now implemented) and
  the seed of v0.3.

### Changed
- Plugin registers 12 tools (was 6 in v0.1.1).
- v0.1.x URL-derived agentId is now a fallback; the key-derived
  ID is preferred when a key is on disk.
- `pyproject.toml` adds `cryptography>=42` as a runtime dep.

### Notes
- Public-share requires both sides to have run `hermes plugins
  install` of v0.2.0+. v0.1.x peers will accept the JSON-RPC
  message but won't recognize the structured `kind: memory_slice`
  payload and will treat it as a plain message.
- The directory at `hermes-a2a.dpmob.com` is scaffolded but not
  deployed yet — see `directory/README.md` for the deploy steps
  once `dpmob.com` DNS is on Cloudflare.

### Security
Two high-severity issues identified by a pre-merge review of the
v0.2 port are fixed in this release (the upstream `feat/v0.2-identity-crypto`
branch had neither):
- `handle_introduce` and `handle_share_public` now route through
  the same `classify_task` approval gate as `handle_send`. A
  memory-share, credential-share, or config-write intent is no
  longer silently shipped — the user gets the approval block
  and must confirm via `a2a_bridge_confirm`. Closes the gap where
  v0.2 outbound paths could bypass the gate that v0.1.x had.
- `handle_receive_public` now resolves `write_to` against
  `HERMES_HOME` and refuses any path that escapes it. A caller
  (or a prompt-injected envelope) passing
  `write_to="../../../tmp/payload"` or an absolute path outside
  the home directory is rejected with an explicit error and
  nothing is written. 4 new regression tests in
  `plugins/a2a_bridge/tests/test_tools.py::TestV02SecurityGates`.

### Not in this port
- `plugins/a2a_bridge/tests/e2e_demo.py` is intentionally NOT in
  the release. It mutates live state on a .2/.3 agent pair and
  has no dry-run mode; it stays on the `feat/v0.2-identity-crypto`
  branch for development use. Re-running it requires a fresh
  sandbox.

## [0.1.1] — 2026-10-04

### Added
- **`identity` module** — `agent_id_for(url)` produces a stable, short
  agent identifier (currently a SHA-256 fingerprint of the normalized
  URL; will become a key-derived ID in v0.2 with ed25519). Survives
  bearer-token rotation. New tests cover URL normalization,
  stability, and prefix customization.
- **`policy` module** — central allowlist at
  `~/.hermes/a2a_bridge/public.yaml` with three visibility levels
  (`public_all` / `public_approved` / `deny`). Two-layer visibility:
  the data side (frontmatter in the data file) and the policy side
  (the allowlist) must agree for content to be shareable. Default
  deny for unmarked content.
- **`a2a_bridge_shareable` tool** — dry-run helper that lists every
  slice in the allowlist and reports whether each is shareable with
  a given peer. Read-only; no actual sharing yet.
- **`ROADMAP.md`** — design for v0.2 (ed25519 identity, meeting
  protocol, share tools, Cloudflare Pages directory). This is the
  single-source-of-truth for what's next; the in-tree `references/`
  docs explain how to use what exists.
- **107 tests** (was 54): +19 identity, +27 policy, +7 shareable
  dry-run.

### Changed
- Plugin now registers 6 tools (was 5). `plugin.yaml` and
  `__init__.py` updated to include `a2a_bridge_shareable` in
  `provides_tools`.

### Notes
- v0.1.x identity is URL+bearer-token; rotating the token invalidates
  meetings. v0.2 adds ed25519 so identity survives token rotation.
- The actual share / request tools are deferred to v0.2 because
  without ed25519 signatures they would be forgeable.

## [0.1.0] — 2026-10-03

### Added
- First public release.
- Five tools: `a2a_bridge_send`, `a2a_bridge_confirm`, `a2a_bridge_audit`,
  `a2a_bridge_list_peers`, `a2a_bridge_history`.
- Heuristic approval gate detecting `memory_share`, `credential_share`,
  and `config_write` patterns; returns a structured approval block
  before any sensitive task fires.
- Audit-log reader and markdown-table formatter for `~/.hermes/a2a_audit.jsonl`.
- Two-party memory-share protocol reference (`references/memory-share.md`).
- TLS setup guide with Caddy + Let's Encrypt recipe (`references/tls-setup.md`).
- Agent Plugins v1 manifest at the repo root (`plugin.json`).
  The actual tool registration is still driven by the in-process
  `plugin.yaml` loader; the `plugin.json` makes the package discoverable
  in catalogs.
- 54 unit tests + 9 live-peer integration tests, all passing.
  Integration tests are opt-in: skip when `HERMES_A2A_TEST_TOKEN` is unset.
- GitHub Actions CI on Python 3.10, 3.11, 3.12.