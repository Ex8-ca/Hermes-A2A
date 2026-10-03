# Changelog

All notable changes to Hermes-A2A are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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