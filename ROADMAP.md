# Roadmap to v0.2 — Public memories, public skills, agent identity

**v0.2.0 SHIPPED 2026-10-04** (tag `v0.2.0`, commit `2fd2598`). This
document is preserved as the design history for the v0.2 release —
how we got there, what the alternatives were, and what's still
immutable by design.

The full discussion is preserved in the conversation log; this is the
single-source-of-truth writeup so future contributors (including future-us)
have a place to look. For the post-v0.2 work, see the v0.3 section
at the bottom of this file.

## What we're building

Two agents that have never met should be able to:

1. Discover each other (a thin directory, not a chat protocol).
2. Recognize each other across token rotations and card re-issues (a stable
   agent ID backed by a public key).
3. Establish a meeting on first contact with **explicit owner approval**
   (no auto-accept).
4. Share a subset of memories and skills that the originating owner has
   explicitly marked as public, scoped per-peer.
5. Revoke or expire the meeting at any time, on either side.

## The design

### Identity

- **v0.1.x (today):** identity is the agent's URL + bearer token combination.
  Rotation invalidates meetings. We document this as a known sharp edge.
- **v0.2:** each agent generates an ed25519 keypair on first run. The
  **public key** is added to the Agent Card as a new field. The
  **agentId** is derived as the first 16 bytes of the SHA-256 of the
  public key, base32-encoded with an `agent_` prefix
  (e.g. `agent_8f3a7c2d9b1e4f5a`). Meetings are keyed on agentId, not
  URL, so they survive bearer-token rotation and DNS changes.

  Implementation: add `cryptography` as a runtime dependency
  (`pyproject.toml` → `dependencies = ["cryptography>=42"]`). Generate on
  first run, store the private key at
  `~/.hermes/a2a_bridge/identity.key` (mode 0600). On every send, attach
  the agentId in an `X-A2A-Agent-Id` header. On receive, verify the
  remote's publicKey against the agentId they claim.

### Public-marking (two layers)

Both layers must agree for content to be shareable. Default for unmarked
content: **private**.

**Layer 1 — Data side (frontmatter).** The data file itself declares
visibility:

```markdown
<!-- hermes:visibility=public_all audience="*" -->
## 2026-10-02 — Project Alpha notes
- ...
```

or

```
# ~/.hermes/skills/cool-md-formatter/SKILL.md
---
name: cool-md-formatter
visibility: public_approved   # default is private
---
```

Visibility values:

- `public_all` — shareable with every peer on the met list.
- `public_approved` — shareable only with explicitly-approved peers
  (the central allowlist's `approved_peers` set, see below).
- `private` (default) — never share, regardless of any other policy.

**Layer 2 — Policy side (central allowlist).** A single reviewable file
at `~/.hermes/a2a_bridge/public.yaml`:

```yaml
# The default level for content that has no explicit visibility frontmatter.
default_visibility: private

# Slices explicitly enumerated (overrides data-side default; required
# for per-peer scoping).
slices:
  memories:
    - name: project-alpha
      path: MEMORY.md
      heading_anchor: "## 2026-10-02 — Project Alpha"
      level: public_all
    - name: robot-design
      path: MEMORY.md
      heading_anchor: "## 2026-10-01 — Robot design notes"
      level: public_approved
      approved_peers:
        - agent_8f3a7c2d9b1e4f5a   # only this peer may receive
  skills:
    - name: cool-md-formatter
      path: skills/cool-md-formatter
      level: public_all

# Meet the world-level default: which peers are auto-eligible for
# public_all content?
approved_peers: []   # empty by default; owner must add explicitly
```

The plugin composes: a slice is shareable with peer X iff
`slice.visibility == "public_all"` AND X is on the met list, OR
`slice.visibility == "public_approved"` AND X is on
`slice.approved_peers`. Both layers must agree.

**Why two layers?** Single layer is too easy to misconfigure. Frontmatter
means the data author thought about visibility when they wrote it. The
central allowlist is the owner's last-line review. If they disagree, the
content is private. This matches Mac OS file permissions + ACLs.

### Meeting protocol (always require owner approval)

```
1. Owner says: "introduce me to <peer_url_or_id>"
2. Agent A sends: { "method": "introduce", "from": A.id,
                    "agent_card_url": A.card_url, "public_key": A.pub,
                    "intent": "share memories + skills" }
3. Peer B's owner sees the introduction request:
   - Shows: A's name, A's public key fingerprint, A's declared
     intent, A's reputation (if any — from the directory, v0.2.5)
   - Three buttons: "Approve (read public only)" /
                   "Approve (read+write public)" / "Deny"
4. B's owner approves; B sends:
   { "method": "introduce_ack", "from": B.id,
     "agent_card_url": B.card_url, "public_key": B.pub,
     "granted_capabilities": ["read_public"],
     "consent_until": "<ISO8601 + 30d>" }
5. A's owner sees the ack, with the granted capabilities and
   consent window. Same approve/deny gate. Click "Confirm meeting."
6. Both sides persist: { peer_id, peer_url, public_key,
                          established_at, granted_caps,
                          consent_until, last_seen }
```

Consent TTL is 30 days by default. After expiry, the meeting is dormant
until the owner re-confirms. Revocation is unilateral and immediate on
either side.

Graceful degradation: if the remote peer doesn't recognize
`method: "introduce"`, the standard JSON-RPC "method not found" comes
back and the sender logs "peer doesn't support a2a-bridge yet, falling
back to raw a2a_call."

### Public-share tools

Once a meeting is established and both sides have at least
`read_public` granted:

- `a2a_bridge_share_public(peer, slice_name)` — sender side. Resolves
  the slice via frontmatter + allowlist, packages a
  `memory_slice`-shaped JSON payload (kind, from_peer, slice_name,
  contents), signs with the agent's private key, and posts it as a
  SendMessage with `metadata.slice_name` and `metadata.slice_signature`.
- `a2a_bridge_request_public(peer, slice_name)` — receiver side. Sends
  a structured request; remote composes the same payload and replies.
  Receiver verifies the signature against the meeting's stored
  public key before writing to its own MEMORY.md.

The receiver never silently writes. The receiving agent's own approval
gate + prompt-injection filter inspects the request, and the owner's
MEMORY.md entry is appended with provenance
(`source: peer=agent_... received=... task=... signature=...`).

### Thin directory (Cloudflare Pages)

Host: `hermes-a2a.dpmob.com`. Three pages:

- `/` — landing. Project description, install command, FAQ.
- `/agents.json` — JSON catalog of opted-in agents. Each entry:
  `{ agent_id, name, agent_card_url, public_key, declared_at,
     last_verified, opt_in: true }`. Updated by submitter signing
     a one-line JSON with their private key and POSTing to a
     Cloudflare Worker (write-restricted; see below).
- `/agent/<id>.html` — per-agent profile page. Renders the name,
  declared skills, and a "send a meeting request" button that
  posts to the agent's card URL.

**Write policy:** Cloudflare Worker behind the same origin accepts
submissions only when the request body has a valid signature from
one of a known list of approver public keys (you + collaborators
you've added). Rate-limited to 10 submissions / hour / IP.

Static hosting cost: zero (Cloudflare Pages free tier).

## Implementation order

1. **v0.1.x foundation (this work, ships first):**
   - URL+token identity primitive (no cryptography).
   - Central allowlist at `~/.hermes/a2a_bridge/public.yaml` (so the
     user has a place to review, even before slices are shareable).
   - Public-marking helper tool that scans MEMORY.md and skills/ for
     frontmatter, validates against the allowlist, reports what would
     be shareable. Dry-run only — no actual sharing yet.
   - This document (`ROADMAP.md`).
2. **v0.2 (next):**
   - Add `cryptography` dependency.
   - Identity: ed25519 keypair, publicKey in card, agentId derivation.
   - Meeting protocol: introduce / introduce_ack / consent TTL.
   - Public-share tools: share_public, request_public.
   - Cloudflare Pages directory: static site + Worker for write-restricted
     submissions.

## Open questions (for v0.2 design phase)

- Should the directory's catalog be signed by us (the directory
  operator) so users can verify "the operator hasn't tampered with
  the catalog"? Probably yes — a daily-signed manifest of all entries
  is enough.
- Do we need a "revocation list" the directory serves, so a peer can
  say "I revoked my public key at time T" and other agents learn about
  it? Yes, probably. CRLite-style.
- How do we handle the case where a private key is compromised?
  Same as any other key: rotate, publish a new public key, mark
  the old one revoked in the directory.

## Why we're doing it this way

- **Default-deny everywhere.** Public-marking is opt-in at every layer.
  Unmarked content is private by default; unapproved peers can't see
  anything; the central allowlist is the owner's last line.
- **Stable identity that survives token rotation.** Bearer tokens are
  operational credentials; identity is a separate concern. ed25519
  gives us that separation cleanly.
- **No auto-accept.** The cost of clicking "Approve" once is
  negligible. The cost of auto-accepting a malicious peer is unbounded.
- **Two-layer visibility.** The data side says what the author
  intended; the policy side says what the owner allows. Both must
  agree.
- **Decentralized but with optional directory.** The protocol works
  peer-to-peer with no infrastructure. The directory is a convenience
  layer for discoverability, not a requirement.

---

# Roadmap to v0.3 — Real sharing, replay defense, and operator rotation

This document tracks the open items below v0.2.0, prioritized for the
next iteration. Each item has a one-line threat model, the change
required, and a TDD test plan.

## v0.2.0 shipped (recap)

Plugin v0.2.0 (tag `v0.2.0`, commit `2fd2598`) is live on
`github.com/Ex8-ca/Hermes-A2A`. It adds:

- ed25519 identity layer (`keyring.py`, `~/.hermes/a2a_bridge/identity.key`)
- Meeting protocol with consent TTL and unilateral revoke
- Public-share tools (`share_public`, `receive_public`) with provenance
- Two security fixes: approval-gate on outbound v0.2 paths, path-traversal
  guard on `write_to`

171 plugin tests pass. The e2e demo (`tests/e2e_demo.py`) stays on a
detached branch because it mutates live state.

The directory v2 (4 cycles, 35 tests) is independently live at
https://hermes-a2a.dpmob.com/.

## What's outstanding (prioritized)

### 1. Real slice fetching in `share_public` (medium)

**Today:** `handle_share_public` builds an envelope with a literal
placeholder string. The receiver gets the envelope but the actual
memory contents never travel. v0.3 needs to actually fetch the
slice body (from a memory store the operator marks as public) and
serialize it into the envelope before signing.

**Changes required:**
- `slice.py`: replace the placeholder with a real read of the
  slice source. The source is currently a file path; v0.3 may
  use the directory's public-marking (memory + skills) or a new
  shared-memory abstraction.
- `tools.py:handle_share_public`: read the slice via `slice.py`,
  serialize into the envelope, then sign.
- `tools.py:handle_receive_public`: deserialize the slice body
  from the envelope (instead of treating `envelope` as a generic
  blob) and write to the target with a `<!-- hermes:from=... -->
  provenance` comment.

**Threat model:** the slice source is gated by `policy.py`'s
`public.yaml` allowlist. The share handler reads the source, signs
the canonical bytes, and sends. A second-layer guard: the operator's
approval gate is already in place from the v0.2 port.

**TDD plan:**
- `test_slice.py`: add a test that builds a real envelope from a
  fixture file, parses it on the other end, and gets back the
  exact bytes (XSS-checked).
- `test_tools.py:TestV03SliceFetch`: integration test that
  `handle_share_public` reads a fixture, signs, and `handle_receive_public`
  writes it to a tmp file with the provenance comment.

**Effort:** 1 cycle (~200 lines + tests). The biggest risk is the
"what's a slice?" definition — v0.3 must commit to a serialization
format. JSON-per-line (one memory item per line) is the simplest;
YAML is overkill.

### 2. Replay protection on signed envelopes (medium)

**Today:** `parse_envelope` and `parse_incoming` verify the signature
but never check the envelope's `sent_at` field. An attacker who
captures a signed `introduce` or `memory_slice` can replay it
indefinitely.

**Changes required:**
- `handshake.py:parse_incoming` and `slice.py:parse_envelope`:
  add a `max_age_seconds` parameter (default 300, i.e. 5 minutes).
  Reject envelopes where `sent_at` is older than `now - max_age`
  or more than `max_age` in the future (clock skew tolerance).
- Document the choice in the README. The 5-minute window is a
  trade-off: shorter is safer (replay window) but breaks legitimate
  slow clients; longer is more forgiving but exposes a longer
  replay window. The current 30-day meeting-TTL is independent.

**Threat model:** a passive network observer (or a compromised log
store) replays a captured `introduce` envelope. The window is bounded
by `max_age_seconds`. Clock skew of more than 5 minutes is rare in
modern environments but not impossible; the future-clock tolerance
is the same `max_age_seconds` to keep the implementation simple.

**TDD plan:**
- `test_handshake.py`: a test that signs an envelope, then waits (or
  fakes the clock to) `max_age + 1` seconds and asserts parse
  rejects it.
- A test that signs with `sent_at = now + max_age + 1` and asserts
  parse rejects the future-dated envelope.

**Effort:** half a cycle. The challenge is `sent_at` is set by the
sender; the receiver needs a reliable clock. If the receiver's
clock is skewed, legit envelopes look stale. A short window (5
minutes) is the mitigation; logging rejected replays for operator
audit is the detection.

### 3. `from_public_key` cross-check on receive (low)

**Today:** `handle_introduce_respond` and `handle_receive_public`
verify the signature against `envelope["from_public_key"]`. They do
not cross-check that `from_public_key` matches the meeting record's
`peer_public_key` (the v0.2 branch's design was implicit on this).

**Changes required:**
- After signature verification, look up the meeting by sender_id.
  If found, compare `m.peer_public_key` to `envelope["from_public_key"]`.
  Reject if they differ (and the meeting record is the source of
  truth for who the peer is).

**Threat model:** within a single meeting `agentId`, an attacker
who can produce a valid signature under a key they control (via
key collision, which is computationally infeasible for ed25519, OR
via a flaw in `parse_envelope`'s self-consistency check) could
masquerade as the same agentId with a different key. The cross-check
makes the binding explicit.

**Why this is low-priority:** the agentId is `sha256(pubkey)[:8]`
and an attacker who can produce a valid signature under a key
matching the agentId is the agent. Practically not exploitable
without a separate crypto break. The cross-check is defense in depth.

**TDD plan:**
- `test_handshake.py`: a test that sets up a meeting with peer
  key `K1`, then signs an envelope with a different key `K2` (also
  valid for the same agentId by construction, since agentId is
  derived from the public key — this test may need a different
  approach: generate two distinct agentIds, sign the envelope with
  the second's key, but claim the meeting is with the first).
  Assert parse_incoming rejects.

**Effort:** quarter cycle.

### 4. Operator-rotation in the directory (low)

**Today:** the directory at https://hermes-a2a.dpmob.com/ has a
ROOT_SYSTEM_POLICY (Cloudflare Pages env var) listing two operators.
Adding a third, removing one, or rotating a key requires updating
the secret and triggering a redeploy. There's no UI for it and
no audit log of who changed what when.

**Changes required (v0.3, but might be out of scope for the plugin
repo — it would live in `directory/operator/`):**
- A small `directory/operator/policy_rotate.py` script that:
  - Reads the current `ROOT_SYSTEM_POLICY` from the Cloudflare API
  - Adds/removes/rotates the operator
  - Writes back
  - Logs the change to a local audit log
- The script would be called manually, like `make_submission.py` and
  `render_agents.py`. It does NOT change the v1.1 env-driven allowlist
  in `submit.js`; it just provides a CLI for operators to manage it.

**Threat model:** a compromised operator key would let an attacker
list and approve entries indefinitely. Rotation must be straightforward
enough that operators do it regularly.

**Effort:** half a cycle, if scope-creep limited to the CLI. (The
directory's `submit.js` is already correct — multi-approver, fail-closed
on empty list, JSON-string env support.)

### 5. v0.2.x → v0.3 churn (process)

**Today:** v0.2.0 was just shipped; v0.3 will be the next release.
Each iteration has been a single commit with full TDD coverage and
a fresh git tag. The cadence has been: identify a threat → write the
failing test → write the minimal code → verify on the live pair
(`.2` and `.3`) → tag and release. Continue this.

**The risk to the cadence:** v0.3 will touch *both* the plugin
(slice fetching, replay) and the directory (operator rotation).
That's two scopes. Recommendation: ship v0.3.0-plugin (items 1-3
above) and v0.3.0-directory (item 4) as separate tags so each is
independently roll-back-able.

## Test counts at v0.3.0

| Suite | Tests | Notes |
|---|---|---|
| Plugin unit | 201 | +17 from v0.2.0 (replay window + cross-check + fetch + round-trip) |
| Plugin e2e | 1 (skipped) | `tests/e2e_demo.py` mutates live state; not in CI |
| Directory (Node) | 35 | unchanged from v0.2.0 |
| Directory (Python) | 12 | new `test_policy_rotate.py` covers the operator-rotation CLI |

## v0.3.x shipped (recap)

v0.3.0–v0.3.3 are tagged and live. What's actually shipped vs. what was in the v0.3 plan above:

- **v0.3.0** — replay protection (item 2), real slice fetching + envelope/meeting cross-check (items 1, 3, partially — fetch is done, cross-check is done), operator-rotation CLI (item 4).
- **v0.3.1** — gateway-service wrapper bugfix (`scripts/install-gateway-service.sh` no longer blocks the systemd unit waiting for the gateway to exit; it `setsid`s the gateway, writes the PID, and exits).
- **v0.3.2** — directory landing page redesign (chillygeek-faithful, dark theme, Inter, cyan→purple gradient on focal points, sticky header, 3-up agent card grid, terminal-style install block with v0.3.1).
- **v0.3.3** — operator-signed deletion path (`/delete` Function + `delete_entry.py` CLI + 7 tests), plus reachability fix (`isPrivateOrLoopbackHost()` extended to Tailscale `100.64/10` + `*.ts.net`; `submit.js` skips the liveness probe for private hosts; falls back to GET on HEAD 405/501). Live catalog went from 7 entries (5 stale test artifacts + desktop_2 + parity_smoke) to **2 clean entries**: `desktop_2` (`.2`) and `ai5080` (`.3`).

## v0.4 — Tailscale discoverability (in progress)

**Scope:** the directory already accepts `*.ts.net` URLs (v0.3.3 reachability fix). What's missing is operator-side tooling so the operator doesn't hand-type the MagicDNS URL.

**Plan:**

1. `directory/operator/discover_tailscale.py` — `tailscale status --json` parser. CLI surface: `--self` (print local MagicDNS FQDN), `--peers` (list online peers), `--json` (full filtered status), `--refresh-cache` (write to `~/.hermes/.tailscale-cache.json`).
2. `--auto-tailscale` flag on `make_submission.py` — calls `discover_tailscale.py --self` and builds `agent_card_url = http://<MagicDNS>:9900/.well-known/agent-card.json`. `--port` overrides the default. Operator still controls `--name` and `--agent-id`.
3. README section "Tailscale discoverability" with the operator flow.
4. Tests: ~12 Python tests for `discover_tailscale.py` (mocked subprocess) + 1 for `make_submission.py --auto-tailscale`.
5. CHANGELOG + version bump to 0.4.0.

**Manual step (user, after deploy):** re-submit `desktop_2` and `ai5080` with `--auto-tailscale` so the live entries point at the MagicDNS URLs (`http://minisforum-desktop.taila6e2e.ts.net:9900/...` and `http://ai5080.taila6e2e.ts.net:9900/...`). This is a separate decision because the v0.3.3 entries were the post-cleanup baseline; v0.4 is the operator-side tooling to *make* the migration easy.

## v0.4+ — What's next (prioritized)

### A. End-to-end live test (right after v0.4 ships)

Run the full A2A round-trip between `.2` and `.3` over both the LAN (`192.168.1.x:9900`) and the tailnet (`*.ts.net:9900`) to confirm v0.3.0's protocol features (replay window, cross-check, real slice fetch) work end-to-end against the live pair. Document the result.

### B. Directory v0.4.1 — schema additions (low) — done in v0.4.1

The `agent_card_url` field currently stores any URL. Add an optional `transport` field so a discoverer can see at-a-glance whether an entry is reachable over Tailscale (`transport: "tailscale-magicdns"`) vs LAN (`transport: "lan"`) vs public HTTPS (`transport: "https"`). Pure schema; no behavior change. The directory's per-agent SSR pages would render the transport as a tag chip.

Shipped in v0.4.1: `classifyTransport()` in `pages/functions/_validate.js`
infers the value from `agent_card_url` (operator doesn't pass it);
`/list?transport=…` is the case-insensitive filter; the per-agent
SSR pages render a subtle `.transport-chip` next to the agent name
(cyan for tailscale-magicdns, green for https, gray for lan). Existing
pre-v0.4.1 entries (desktop_2, ai5080) keep working — they pick up
the chip the next time they're re-submitted.

### C. v0.4.1 — directory v2 transport fallback (low)

The current `a2a_call` / `a2a_discover` tools just HTTP the agent_card_url. If the URL is a MagicDNS name and the caller's machine isn't on the same tailnet, the call hangs (DNS resolves, but the IP is unreachable from the caller's network). Add a clear "peer unreachable" error that says "this agent is on a tailnet you don't have access to" instead of the generic HTTP timeout.

### D. v0.5 — directory v3 (Tailscale-native first-class)

The `tailscale` discovery is currently operator-side. v0.5 makes the directory Tailscale-aware at the protocol level: a discoverer running `tailscale status` locally can ask the directory "who on my tailnet speaks A2A?" without a separate submission step. This means the directory would need to validate `agent_card_url` against the operator's tailnet, or accept a Tailscale API key. Out of scope for v0.4; mentioned for completeness.

### E. Per-agent SSR refresh on `/list` mutation (low)

The per-agent pages (`pages/agent/<id>.html`) are rendered at build time by `render_agents.py`. The Cloudflare Function `pages/functions/agent/[id].js` reads from KV at request time. After a v0.3.3-style delete, the static HTML files for the deleted agents still exist on disk but the Function would 404 them. Cleanup: add a `--prune` flag to `render_agents.py` that deletes the static HTML for any agent_id not in the current catalog. Low priority because the stale files are dead code, not user-visible.

### F. v0.4.x — multi-tailnet directory (v3)

The directory currently has one root, one KV namespace, and one `ROOT_SYSTEM_POLICY` env var. A multi-tailnet directory would host multiple tailnets' worth of agents in a single KV namespace, partitioned by tailnet identifier. The directory's URL stays the same; the schema gains a `tailnet` field. Out of scope until someone asks for it.

## Test counts at v0.4.0 (target)

| Suite | Tests | Notes |
|---|---|---|
| Plugin unit | 189 | unchanged from v0.3.0 (the "201" in earlier notes was a release-quote that didn't match the actual collection) |
| Plugin e2e | 1 (skipped) | unchanged |
| Directory (Node) | 43 | +7 Tailscale cases in `validate.test.js` |
| Directory (Python) | 25 | was 12; +12 `test_discover_tailscale.py` + 1 `test_make_submission_tailscale.py` |
| **Total** | **258** | |

## What this document is NOT

It's not a contract. Items here are ordered by the threat model
they close, not by deadline. If a v0.4 user reports an issue
that's not on this list, the list gets reordered; if an item here
turns out to be unworkable, it gets dropped with a note in the
CHANGELOG explaining why.