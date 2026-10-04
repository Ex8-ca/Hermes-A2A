# Changelog

All notable changes to Hermes-A2A are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — memex8 slice transport

### Added — Cross-agent memex8 memory sharing (v0.6.0)

Three new tools and a new envelope kind so an agent can ship specific
memex8 memories to another agent without leaking the rest of its store.
The trust model is the same as the filesystem slice transport
(`a2a_bridge_share_public` / `_receive_public`): signature on the
envelope, meeting record with `read_public`, central allowlist.
What's new is the **visibility** check on the *data* itself.

**New envelope kind: `memex8_memory_slice`**

Carries a list of memex8 memory IDs and the sender's memex8 base URL.
The receiver re-fetches each memory from the sender's memex8 over
HTTP and re-checks `visibility == "public"` before writing anything
to disk. This is the part that fixes the data-side leak: a sender
who flips a memory to private between sign and delivery causes the
receiver to refuse the whole slice.

**New tools:**

- **`a2a_bridge_list_memex8_public(limit, offset)`** — discovery.
  Hits `GET /api/v1/memories/public` on the local memex8 and prints
  a readable summary so the agent knows what's shareable.
- **`a2a_bridge_share_memex8(peer, slice_name, memory_ids)`** — sender.
  Pre-flights every memory ID locally, runs the same per-peer
  allowlist + meeting record check as `share_public`, signs a
  `memex8_memory_slice` envelope, and dispatches through the
  underlying a2a_call (with the existing approval gate).
- **`a2a_bridge_receive_memex8(envelope, write_to)`** — receiver.
  Verifies signature, cross-checks the envelope's public key against
  the meeting record, re-fetches each memory from the sender's
  memex8 base URL, re-validates `visibility=public`, then appends
  to `write_to` (default `MEMORY.md`) with a provenance comment.

**New module: `plugins/a2a_bridge/memex8_client.py`**

Tiny `requests`-based client with three methods (`list_public`,
`get_memory`, `fetch_memex8_slice`). Fail-closed: every transport
or 4xx error raises `Memex8ClientError` so callers don't have to
remember which path returns `None`. New dep: `requests>=2.28` in
`pyproject.toml` (already on the memex8 plugin's dep list).

**New env vars** (optional, only the memex8 tools read them):
- `MEMEX8_API_BASE` (default `http://localhost:8080`)
- `MEMEX8_API_KEY` (bearer token)

**Tests:** 12 new tests in
`plugins/a2a_bridge/tests/test_memex8_slice.py`. Covers envelope
build/parse round-trip, signature tampering, non-http URL refusal,
empty `memory_ids` refusal, client HTTP behavior, sender-side
preflight on private memory, meeting-record enforcement on the
sender side, and the receiver's private-after-refetch refusal.
Full suite: **210/210 pass** (was 198 before this change).

**Depends on:** the memex8 side of this work — a memex8 server at
v1.2.0+ with the `visibility` field on `MemoryPoint`, the
`GET /api/v1/memories/public` discovery endpoint, and the
`visibility` field on `POST /api/v1/memories`. Without those, the
sender preflight will fail with "memex8 unreachable" and the
discovery tool will return an empty list.

## [Unreleased] — README + third-party demo

### Added

- **`scripts/discovery_client_demo.py`** — a public-network
  third-party client demo. Hits `hermes-a2a.dpmob.com`, lists the
  catalog, applies tailnet/transport filters, fetches the per-agent
  SSR page, and demonstrates the v0.4.3 contextual error when a
  caller hits a MagicDNS agent from a non-tailnet host. Runnable
  with no Hermes / Tailscale configuration; pure stdlib. Exit
  code 0 on success. This is the third-party integration test
  for v0.4.3 + v0.5.2.

- **README refresh for v0.5.2** — the on-repo README was pinned
  at v0.1.0 and didn't reflect v0.2.0-v0.5.x. Rewritten to current
  version with:
  - Live "what's new" table back to v0.2.0
  - Three install paths (plugin install / pip / manual drop-in)
  - Directory section: endpoints, filter examples, transport / tailnet
    semantics, third-party discoverer walkthrough
  - Peer-unreachable error UX section (v0.4.3)
  - Tailscale Funnel listed as a TLS option
  - Updated test counts (286)
  - Full project layout tree (was 12 files, now 50+)

## [0.5.2] — 2026-10-04

### Added — Directory per-tailnet discovery (v0.5 first slice)

The `/list` endpoint gains two new orthogonal query parameters for
cross-tailnet discovery:

- **`?tailnet=<name>`** — case-insensitive match against the tailnet
  suffix in `entry.agent_card_url`'s host. For a host like
  `ai5080.taila6e2e.ts.net`, the tailnet is `taila6e2e`. Substring
  match if the param starts with `.` (e.g. `?tailnet=.ts.net` matches
  every MagicDNS entry across all tailnets). Exact match otherwise.
  LAN / public hosts return no match (no tailnet to match against).

- **`?reachable_via=<value>`** — alias for `?transport=`, named from
  the discoverer's perspective. Same semantics. If both are present,
  `?transport=` wins.

Both filters compose with `?transport=`. The response shape gains
a `filters` field echoing the active params for debugging.

This is the first slice of v0.5: anyone on any tailnet can now
filter the public directory to "show me only entries on tailnet
X". The directory itself remains Cloudflare-hosted at `hermes-a2a.dpmob.com`
and unchanged for cross-tailnet visibility — entries from any
tailnet are visible to anyone. The transport field tells the
discoverer what network they'd need to be on to actually reach
the agent.

**Follow-up slices (planned for v0.5.3+):**

- v0.5.3 — Tailscale Funnel mirror of the directory at
  `dir.taila6e2e.ts.net` (same KV, second front-door on `.2`,
  reachable from inside the tailnet for faster discovery).
- v0.5.4 — Submitter-side smoke check (HEAD probe of `agent_card_url`
  from the directory edge, recording `lastseen` on each entry).

**Tests:** 7 new in `directory/tests/list.test.js`. Directory Node
test count: 55 → **62**.

## [0.4.3] — 2026-10-04

### Added — Peer-unreachable error UX

When the Hermes A2A platform's `a2a_call` returns a generic
network-error string ("urlopen error timed out", "Connection
refused", "Name or service not known", etc.) and the peer URL
points at a Tailscale MagicDNS name or a private/LAN address,
the user used to see a cryptic urllib-style message with no hint
about what network they need to be on. The new
`_classify_peer_unreachable()` helper in `tools.py` substitutes a
contextual error that names the specific network requirement.

**The two new override messages:**

- `tailscale-magicdns` → "Error: peer is on a Tailscale tailnet
  (URL: <url>). Your host is not on that tailnet, so the A2A call
  could not reach it. Run `tailscale status` to check your tailnet
  membership, or ask the peer operator to add your host to the
  tailnet's ACL. (Original error: <raw>)"

- `lan` → "Error: peer is on a private network (URL: <url>;
  RFC1918 / loopback / link-local / Tailscale CGNAT). Your host is
  not on the same network, so the A2A call could not reach it.
  Confirm your machine is on the same LAN, VPN, or Tailscale
  tailnet as the peer. (Original error: <raw>)"

**What is NOT overridden:**

- Public URLs (`https://example.com` etc.) — the platform's
  generic error is fine; the issue is the URL itself, not the
  caller's network.
- Auth errors (`rejected auth`, HTTP 401/403) — these are
  credentials problems and the platform's message is more
  specific to the fix path.
- Successful replies — passthrough as before.

**Where it's applied:**

- `handle_send` — the main `a2a_bridge_send` path
- `handle_confirm` — the post-approval send path
- `handle_introduce` — sending an `introduce` envelope
- `handle_introduce_respond` — sending an `introduce_ack`
- `handle_share_public` — delivering a memory slice envelope

`handle_receive_public` (inbound) is not touched — it doesn't
make outbound HTTP calls.

**Classification helper:** `_is_private_or_loopback_url(peer_url)`
classifies a URL into one of three classes (`tailscale-magicdns`
| `lan` | `public`). It uses Python stdlib's `ipaddress` module
for IPv4/IPv6 ranges (with manual handling of the Tailscale
`100.64/10` CGNAT range, which stdlib doesn't know about) and
excludes the IPv6 documentation range (`2001:db8::/32`) which
stdlib flags as private but isn't a real network.

Tests: **9 new** in `tests/test_peer_unreachable.py` (was 0 in
this file). Plugin test count is now **198** (was 189).

## [0.4.2] — 2026-10-04

### Added

- **`render_agents.py --prune`** — new flag that deletes per-agent
  HTML files in `pages/agent/` for `agent_id`s that are no longer in
  the live catalog. The `_template.html` is preserved. Useful after a
  v0.3.3-style delete to keep the build-time artifacts in sync with
  the live KV namespace. The v0.3.3 cleanup left 5 stale HTMLs on
  disk (`agent_2f4b8e9d11a7c6a5`, `agent_83c2d59a6c821f7b`,
  `desktop_2_v2_selfsign_test`, `second_op_test`, `test_https`); this
  release prunes them and ships the new flag to prevent future drift.

## [0.4.1] — 2026-10-04

### Added — Directory transport classification

The directory's per-agent entries gain an optional `transport` field
that classifies the agent_card_url as one of four values:

- `tailscale-magicdns` — host is a Tailscale MagicDNS name (`*.ts.net`)
- `lan` — host is RFC1918 / loopback / link-local / ULA / Tailscale
  `100.64/10` (private network)
- `https` — public HTTPS endpoint
- `http-public` — plain HTTP to a public host (rare; `submit.js`
  refuses this at submit time, so this value only appears for
  manually-created or migrated entries)

**Operator-side:** no change. `make_submission.py --auto-tailscale`
and the manual workflow do not need a `--transport` flag — `submit.js`
infers the value from `agent_card_url` via the new `classifyTransport()`
function in `pages/functions/_validate.js`. A manual `transport`
field in the request body overrides the inference (allowed for
backward compat with v0.3.x operators who wrote entries by hand);
values outside the four-class allowlist are rejected with HTTP 400.

**Discoverer-side:** the `/list` JSON endpoint accepts an optional
`?transport=<value>` query parameter. When present, the response
filters to entries whose `entry.transport` matches (case-insensitive).
When absent, `/list` returns all entries unchanged (the prior contract).

**Per-agent SSR pages** render a small `.transport-chip` next to the
agent name when `entry.transport` is set. Pre-v0.4.1 entries (the
two live entries: `desktop_2` and `ai5080`) don't have the field and
render with no chip — they will pick up the chip the next time
they're re-submitted, since the inference runs from the URL.

CSS adds `.transport-chip` (base) + `.transport-tailscale-magicdns`
(cyan, matching `--accent-cyan`), `.transport-lan` (gray,
`--text-secondary`), and `.transport-https` (green, `#22c55e`).
`http-public` falls back to the base style (rarely rendered).
Subtle palette consistent with the existing `.cap-chip`.

The two render paths:

1. **Build-time** — `directory/render.mjs` (called from
   `directory/render_agents.py` → `render_one.mjs`) bakes the chip
   into the per-agent HTML when `entry.transport` is set, or leaves
   the chip `hidden` when not.
2. **Request-time** — the inline `<script>` in
   `directory/pages/agent/_template.html` (which fetches `/list` to
   pick up fresh `last_verified`) also populates the chip. Same
   allowlist check; the chip stays `hidden` for legacy entries that
   lack the field.

### Tests

- 6 new tests in
  `directory/tests/validate.test.js` cover `classifyTransport()`
  end-to-end: `*.ts.net` (tailscale-magicdns), Tailscale IP
  `100.64/10` (lan), RFC1918 `192.168.x` and `10.x` (lan), public
  HTTPS (https), plain HTTP to a public IP (http-public), and the
  malformed/null-URL paths (returns null).
- 2 new tests in `directory/tests/submit.test.js` prove the
  submit.js integration: a tailnet URL with no explicit `transport`
  gets `tailscale-magicdns` stored, and a LAN URL with an explicit
  `transport: "lan"` stores that value verbatim.
- 3 new tests in `directory/tests/list.test.js` (a new file) cover
  the `?transport=` filter: unfiltered returns all, filtered
  returns only matching, and the filter is case-insensitive.
- 1 new test in `directory/tests/render-agents.test.js` proves the
  transport chip is baked into the per-agent HTML when
  `entry.transport` is set, and stays hidden when it isn't.

Total Node tests: 55 (was 43). Plugin (201) and directory Python
tests (13) unchanged.

### Notes

- The change is **purely additive**. Existing entries without a
  `transport` field still work; the field is inferred on the next
  re-submission. The `/list` endpoint returns them all on
  unfiltered calls; a filtered call simply won't match them.
- The schema change is additive at the wire level: the directory's
  schema section in `directory/README.md` will gain a `transport`
  row in the table.
- No new dependencies. No new functions or tools beyond
  `classifyTransport()`.

## [0.4.0] — 2026-10-04

### Added — Tailscale discoverability for the directory

- **`directory/operator/discover_tailscale.py`** — a standalone helper
  that calls `tailscale status --json` and surfaces the operator's
  local MagicDNS name (`--self`), the online peer list (`--peers`),
  the filtered status as JSON (`--json`), or refreshes a small
  mode-0600 cache at `~/.hermes/.tailscale-cache.json`
  (`--refresh-cache`). Standard library only; no new dependencies.
  Handles `tailscale` missing from PATH, non-zero exits, timeouts,
  and unparsable JSON with clear errors.
- **`make_submission.py --auto-tailscale`** — new flag on the
  existing submitter that uses `discover_tailscale.py --self` to
  pre-fill `agent_card_url` with the host's MagicDNS URL. Precedence:
    * `--card-url` explicit > `--auto-tailscale` (the explicit URL wins,
      a stderr warning notes the conflict).
    * `--name` explicit > `--auto-tailscale` (auto-fill is silent
      when `--name` was passed).
    * `--name` omitted with `--auto-tailscale`: defaults to
      `<host> (operator)`.
  Use `--port N` to override the default 9900 in the auto-built URL.
  Requires `--name` (existing behavior) unless `--auto-tailscale`
  supplies the default.

### Tests

- 12 new tests in
  `directory/operator/tests/test_discover_tailscale.py` cover
  tailscale-missing / non-zero / invalid-JSON paths plus the four
  output modes (`--self`, `--peers`, `--json`, `--refresh-cache`) and
  the online/offline peer split.
- 1 new test in
  `directory/operator/tests/test_make_submission_tailscale.py`
  exercises the full precedence matrix of `--auto-tailscale` end-to-end
  (auto-fill happy path, explicit URL wins, explicit name wins,
  `--port` override, error path).

Total Python tests: 25 (was 12). Plugin (189 actual) and directory
Node (43) test counts unchanged.

### Docs

- New "Tailscale discoverability" section in `directory/README.md`
  documents the operator flow, the `--port` option, the standalone
  `discover_tailscale.py` CLI, and a troubleshooting subsection
  (tailscale not on PATH, host not authenticated, tailscaled hung).
- `make_submission.py` module docstring extended with a `--auto-tailscale`
  example.

### Notes

- The directory-side change for `*.ts.net` URLs (no liveness probe,
  `isPrivateOrLoopbackHost()` extension) shipped in v0.3.3. v0.4.0
  is the operator-side tooling to actually use that path.
- The two live entries (`desktop_2`, `ai5080`) were migrated to
  MagicDNS URLs after this release was committed
  (`http://minisforum-desktop.taila6e2e.ts.net:9900/...` and
  `http://ai5080.taila6e2e.ts.net:9900/...`).

## [0.3.4] — 2026-10-04

### Fixed

- **PyPI release build** — `python -m build` was failing on every release
  since v0.2.0 with `error: Multiple top-level packages discovered in a
  flat-layout: ['plugins', 'directory']`. The top-level `directory/`
  directory (node/web frontend) was being picked up by setuptools' default
  auto-discovery alongside the real `plugins/` package. Added an explicit
  `[tool.setuptools.packages.find]` block in `pyproject.toml` that only
  includes `plugins.*` and excludes `directory*` / `scripts*`.
- **CI test collection** — every `pytest` run on `main` since v0.2.0 was
  failing to collect `plugins/a2a_bridge/tests/*.py` with
  `ModuleNotFoundError: No module named 'plugins'`. CI installs `pytest`
  but not the project (the plugin is drop-in, not pip-installable on CI),
  and pytest's CWD-on-sys.path behavior changed. Added
  `pythonpath = ["."]` to `[tool.pytest.ini_options]` so test files can
  do `from plugins.a2a_bridge import ...` against the repo root.

## [0.3.3] — 2026-10-04

### Added — Directory deletion path

- **`POST /delete` Pages Function** —
  `directory/pages/functions/delete.js`. Accepts signed deletion
  envelopes (`kind: "agent_deletion"`, `agent_id`, `public_key`,
  `submitted_at`, `signature`). Verifies the signature against the
  envelope's `public_key`, then authorizes by matching either an
  operator in `ROOT_SYSTEM_POLICY.approvers[]` (any entry) or the
  stored entry's own `public_key` (self-delete — defense in depth).
  Removes `agent:<agent_id>` from KV and writes a tamper-evident
  marker at `deletion:<iso>:<agent_id>` in the same `AGENTS`
  namespace. The marker is invisible to `/list` (which iterates
  only `agent:`) but readable from the Cloudflare dashboard for
  audit cross-checks against the operator's local log.
- **Operator delete CLI** — `directory/operator/delete_entry.py`.
  Mirrors `make_submission.py`: loads the operator ed25519 key,
  canonicalizes, signs, POSTs to `https://hermes-a2a.dpmob.com/delete`.
  Supports `--dry-run`, `--verbose`, `--submit-url`, `--key-path`,
  `--no-audit`. Appends every successful and failed operation to
  `directory/operator/.deletion-audit.log` (created mode 0600) so
  the operator has a local tamper-evident record.
- **Tests** — `directory/tests/delete.test.js`. 7 cases covering
  operator-delete, self-delete, invalid signature (401),
  unauthorized key (403), missing entry (404), wrong envelope
  kind (400), and GET-on-/delete (405).

### Security

- The deletion envelope has a fixed `kind: "agent_deletion"` guard
  so a `/submit` envelope accidentally POSTed to `/delete` is
  rejected as `missing field: kind` (400). The two endpoints
  never accept each other's wire format.
- Authorization is checked AFTER signature verification: an
  attacker who controls bytes on the wire still has to produce
  a valid ed25519 signature over the canonical envelope before
  the operator-vs-self authorization step runs.

### Notes

- The /delete endpoint is write-only: GET returns 405 with a
  clear error message rather than the static landing page.
- Deletion is non-recoverable from the live directory's read
  path. The marker is preserved for audit only; there is no
  `/undelete` endpoint. Operators wanting a recoverable deletion
  workflow should `submit.js`-update an entry with a sentinel
  `name` and a `description` explaining "withdrawn" rather than
  deleting it (preserves the agent_id and audit trail).

### Operator actions (post-deploy)

- 5 stale test entries deleted from the live directory:
  `agent_2f4b8e9d11a7c6a5` (Hermes Bridge Demo),
  `agent_83c2d59a6c821f7b` (Marc's Operator Test Bot),
  `desktop_2_v2_selfsign_test` (Self-Sign Test Agent),
  `second_op_test` (Second Operator's Agent), `test_https`
  (Test HTTPS).
- `desktop_2` entry's `agent_card_url` corrected to
  `http://192.168.1.2:9900/.well-known/agent-card.json`.
- New `.3 (ai5080)` entry submitted with
  `agent_card_url = http://192.168.1.3:9900/.well-known/agent-card.json`.

### Known blockers surfaced during cleanup

The operator-driven cleanup script ran but hit two pre-existing
issues the user should know about BEFORE deploying:

1. **`/delete` is not yet deployed.** The CLI builds valid signed
   envelopes; pre-deploy, every POST returns Cloudflare's default
   `405 method not allowed` because no Pages Function is bound
   to `/delete`. Deploy the new function first, then re-run.
2. **The Hermes A2A gateway (BaseHTTP/0.6 Python) returns 501 to
   `HEAD` requests**, so the live `/submit` HEAD liveness check
   rejects both `http://192.168.1.2:9900/.well-known/agent-card.json`
   and `http://192.168.1.3:9900/.well-known/agent-card.json`
   with `400 agent_card_url … did not respond 200 to HEAD`.
   The corrected desktop_2 entry and the new .3 entry cannot be
   created via `/submit` until either the gateway is taught to
   respond 200 to HEAD (out of scope for this release) or
   `submit.js` falls back to GET on HEAD failure (would require a
   code change to submit.js, not done here per scope). The
   entries can be written by hand in the Cloudflare dashboard or
   via `wrangler kv key put` with the JSON body below.

### Added post-review — directory reachability

After the v0.3.3 cut, two real blockers surfaced during the live
cleanup that needed the same fix:

  * The A2A gateway (Python `BaseHTTPRequestHandler`) returns
    `501 Unsupported Method ('HEAD')` for HEAD requests, so the
    `/submit` liveness probe fails. `submit.js` now falls back to
    GET on `405`/`501` responses. Real network errors still refuse
    the submission.
  * The directory runs on Cloudflare Pages, which cannot reach
    private network ranges (RFC1918 LAN, `127.0.0.0/8`, `fe80::/10`)
    or Tailscale (`100.64.0.0/10` CGNAT, `*.ts.net` MagicDNS).
    `isPrivateOrLoopbackHost()` in `_validate.js` was extended to
    include Tailscale's IP range and `*.ts.net` / `*.tailscale.us`
    hostnames, and `submit.js` now skips the liveness probe for
    any URL whose host is in these ranges — the operator is
    responsible for the URL being correct, the directory is a
    discovery layer (not a reachability oracle), and discoverers
    do their own reachability check at call time.

Tests: 7 new Tailscale cases in `directory/tests/validate.test.js`
(IPv4 100.64/10 boundaries, MagicDNS suffix match + case
insensitivity, IPv6 `fe80::/10` link-local). All 43 directory
Node tests pass.

Net effect on the live directory: 5 stale test entries removed
(`agent_2f4b8e9d11a7c6a5`, `agent_83c2d59a6c821f7b`,
`agent_parity_smoke_test`, `desktop_2_v2_selfsign_test`,
`second_op_test`, `test_https`); the `desktop_2` entry's
`agent_card_url` is now `http://192.168.1.2:9900/.well-known/agent-card.json`;
and a new `ai5080` entry is live for `.3`.

## [0.3.2] — 2026-10-04

### Changed — Directory landing page

- **New landing-page design.** The flat 5-section page with 55 lines
  of CSS is replaced with a chillygeek-faithful redesign: dark theme
  (`--bg-primary: #0a0b0f`), Inter from Google Fonts, cyan→purple
  gradient on focal points only, sticky header with gradient brand
  mark, 4-card "Find the project" connect grid, 3-up agent card
  grid with capability chips and verified dates, terminal-style
  install block with the v0.3.1 command.
- **Shared `/variant-list.js`** fetches `/list` (KV-backed), falls
  back to `/agents.json` (static seed), and renders into
  `<div id="agent-list" data-mode="...">`. The hero
  `<span data-agent-count>` is updated from the same response so
  each variant can show or hide the count without re-implementing
  the fetch.
- **Variants A, B, C** (`variant-{a,b,c}.html/css`) are checked in
  for future reference. Variant A is promoted to `index.html` +
  `style.css` and is live.

### Notes

- The plugin code, KV binding, `submit.js`, `list.js`, and
  per-agent SSR pages are unchanged. The `/list` endpoint contract
  is unchanged.
- Slop audit (variant A): 1.5/10. Only the brief-mandated
  cyan→purple gradient on the h1 word and primary button (tell 1)
  and Inter as the body face (tell 9) score non-zero. No icon
  toppers, no monument stats, no accent rails, no glassmorphism.

## [0.3.1] — 2026-10-04

### Fixed — Plugin install script

- `scripts/install-gateway-service.sh` — the wrapper script used to
  block in the foreground waiting for `hermes gateway run` to exit
  (which it never does). With `Type=oneshot + RemainAfterExit=yes`
  this meant `systemctl start` would block for up to `TimeoutStartSec`
  seconds and then the service was marked `failed` (timeout) on some
  systemd versions. The fix:

  * The wrapper now `setsid`s the gateway into a detached process
    group, writes its PID to `~/.hermes/a2a-bridge/gateway.pid`, and
    exits 0 immediately.
  * If the gateway process exits within 2 seconds with the per-host
    singleton message ("already serves profile 'default'"), the
    wrapper finds the *existing* gateway listening on port 9900 via
    `ss`, writes *its* PID to the PID file, and exits 0. This makes
    the wrapper safe to run on a host that already has a gateway
    (e.g. one started by the desktop) — it tracks the existing one
    instead of failing.
  * `TimeoutStartSec` lowered to 15s (the wrapper should return in
    under 3s in normal operation).
  * The unit file gets an `ExecStop` that uses the PID file to
    terminate the gateway cleanly on `systemctl stop`, and removes
    the PID file.

  Operators upgrading from v0.3.0 should re-run
  `./scripts/install-gateway-service.sh --uninstall && ./scripts/install-gateway-service.sh --start`
  on each host. The PID file format is forward-compatible.

## [0.3.0] — 2026-10-04

### Added — Plugin

- **Replay protection on signed envelopes** — `parse_incoming`
  and `parse_envelope` now check `sent_at` against the receiver's
  clock and refuse any envelope older than `max_age_seconds` (default
  300s) or more than `max_age_seconds` in the future. The window
  bounds the blast radius of a captured envelope. The check is
  applied to both `introduce`/`introduce_ack` envelopes and
  `memory_slice` envelopes. Operators can extend the window via
  `max_age_seconds=…` for slow networks. `handshake.check_replay_window`
  is a public helper; `handshake.MAX_ENVELOPE_AGE` is the default.
  Replay-window tests cover stale, future-dated, and within-window
  scenarios for both envelope types.
- **Real slice fetching** — `handle_share_public` now reads the
  slice from disk via the new `slice.fetch_contents(slice_, home=…)`
  function instead of sending a placeholder. The function splits
  the source file (relative to `HERMES_HOME`) into `{heading, body}`
  sections at markdown `^#+\s+…` boundaries. If
  `slice_.heading_anchor` is set, only the matching section is
  sent. Path-traversal is refused via the same `is_relative_to`
  guard used for `write_to`. Empty sections (anchor matched
  nothing) are also refused. The receiver sees the same bytes
  the sender had at send time.
- **Cross-check between envelope and meeting record** — both
  `handle_introduce_respond` and `handle_receive_public` now
  refuse envelopes whose `from_public_key` differs from the
  `peer_public_key` stored in the meeting record. Defense in
  depth against a future bug in the agentId/key fingerprint check
  or a key-collision attack. The check only fires when a meeting
  record already exists; new meetings still flow through
  `parse_incoming`'s own fingerprint check.

### Added — Directory

- **Operator-rotation CLI** — `directory/operator/policy_rotate.py`.
  Adds and removes approvers from the central
  `ROOT_SYSTEM_POLICY` JSON that the directory's `submit.js`
  reads. Refuses to remove the last approver (would lock the
  directory out — `submit.js` fails closed). Writes mode-0600
  policy and audit files. Audit log captures who was added or
  removed and when. The CLI does not call the Cloudflare API;
  the operator pastes the resulting JSON into
  `wrangler pages secret put ROOT_SYSTEM_POLICY`.

### Fixed

- `handle_introduce_respond` now passes `sent_at` through to
  `parse_incoming` so the replay-window check can fire on
  user-approved consent responses.
- `handle_introduce_respond` no longer loads the meeting store
  twice (was loading once for the new meeting and once for the
  upsert — kept only the upsert path).

### Tests

- 13 new tests across 4 new test classes:
  - `TestReplayWindow` in test_handshake.py (4 tests)
  - `TestReplayWindow` in test_slice.py (4 tests)
  - `TestFetchContents` in test_slice.py (5 tests, including
    a fetch→envelope→parse round-trip and the path-traversal guard)
  - `TestV02SecurityGates` in test_tools.py grew with 2 new
    cross-check tests (matching-key acceptance + key-substitution
    refusal)
  - `test_policy_rotate.py` is a new test file with 12 tests
    covering add / remove / list / duplicate / last-approver
    / audit-log / file-mode invariants.

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