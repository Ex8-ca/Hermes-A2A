# Roadmap to v0.2 — Public memories, public skills, agent identity

This document describes the design we agreed on (2026-10-04) for cross-agent
public sharing. It is **not implemented yet** — it's the plan that v0.2 will
execute, and the foundation we lay in v0.1.x to make v0.2 a small step rather
than a rewrite.

The full discussion is preserved in the conversation log; this is the
single-source-of-truth writeup so future contributors (including future-us)
have a place to look.

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