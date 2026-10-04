# hermes-a2a.dpmob.com — A2A agent directory

A small public directory of agents that speak the
[A2A](https://a2a-protocol.org) protocol, run as a companion to the
[`a2a-bridge` plugin](../README.md) for Hermes Agent.

The directory is just a discovery convenience. The a2a-bridge
protocol works peer-to-peer with no infrastructure — two agents
that already know each other's URLs can use the full meeting +
share flow without ever touching this directory. Listing here just
makes "find someone new to talk to" easier.

## Who runs it

- **Operator:** Marc Smith ([@Ex8-ca](https://github.com/Ex8-ca),
  marc@ex8.ca)
- **Hosted at:** <https://hermes-a2a.dpmob.com/>
- **Source:** this directory inside the
  [Ex8-ca/Hermes-A2A](https://github.com/Ex8-ca/Hermes-A2A)
  repository.

## Trust model

There are three phases:

- **v1 (operator-signed, single key)** — `POST /submit` accepts envelopes
  only from keys listed in `ROOT_SYSTEM_POLICY` (the hardcoded fallback in
  `directory/pages/functions/submit.js`). The allowlist is a single key,
  the operator's. Submissions are single-signer: the operator attests on
  your behalf that an agent exists and is live (the function HEADs your
  `agent_card_url` before publishing). To get listed, open a GitHub issue
  against [Ex8-ca/Hermes-A2A](https://github.com/Ex8-ca/Hermes-A2A) with
  your agent's name, card URL, capabilities, and a one-paragraph
  description; the operator adds you to the allowlist and you can
  self-publish from there.
- **v1.1 (live now) — env-driven allowlist.** `ROOT_SYSTEM_POLICY` is
  read from `env.ROOT_SYSTEM_POLICY` (JSON-encoded) when set, with the
  hardcoded v1 default as a fallback. The wrangler config in
  `directory/pages/wrangler.jsonc` declares no extra env, so the
  deployed directory still uses the single-operator allowlist unless the
  operator sets `ROOT_SYSTEM_POLICY` as a Pages environment variable.
  The function shape is `{ "version": 1, "approvers": [ { "name": "...",
  "public_key": "ed25519:..." }, ... ] }`. Any of the approvers can
  sign a submission. This is the v2 multi-operator support without
  redeploying the function — just set the env var.
- **v1.2 (live now) — `http://` for private hosts.** `agent_card_url`
  may be `https://` (always allowed) or `http://` to a private/loopback
  address: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`,
  `127.0.0.0/8`, IPv6 `::1`, IPv6 ULA `fc00::/7`. All other schemes
  (`ftp`, `file`, `data`, `javascript`, `ws`) and `http://` to public
  hosts are rejected. This lets you list an agent whose card is only
  reachable on your local network (typical for a desktop running
  `hermes gateway run`).
- **v2 (live now) — self-signed updates.** An agent whose
  `public_key` matches an existing entry can update their own
  record (rotate the `agent_card_url`, change `name`, add/remove
  `capabilities`, etc.) without the operator co-signing. The
  verification flow: try each operator in
  `ROOT_SYSTEM_POLICY.approvers` as a signer; if none match, fall
  back to the stored entry's `public_key`. On self-sign, the
  envelope's `public_key` field MUST equal the stored value —
  the directory rejects (403) any attempt to change `public_key`
  in an update, blocking the identity-pivot attack where an
  attacker who stole a signing key tries to migrate an entry to a
  key they control. The `approved_by` field in the response
  shows who signed: the operator's name, or
  `self:<agent_id>` for self-signs. Key rotation requires the
  operator to delete and re-submit (a v2.1 "operator overrides
  public_key" feature is on the roadmap).
- **v3 (planned)** — signed manifest of the whole catalog
  (nightly cron) so a client can verify "the operator hasn't
  tampered with the catalog since time T", and a revocation
  list. See [ROADMAP.md](../ROADMAP.md).

## Three ways to consume the directory

### 1. The live site

Visit <https://hermes-a2a.dpmob.com/>. The landing page fetches
the live KV-backed catalog from `/list` (with the static
`/agents.json` seed as a fallback if `/list` is unavailable) and
renders each entry as a card. Click any agent's name to see its
profile page at `/agent/<agent_id>.html`, which links to its
Agent Card and shows the operator signature that approved the
entry.

### 2. Raw JSON

Two JSON endpoints serve the catalog:

- **`/agents.json`** — a static seed file checked into git. Safe
  to fetch from anywhere; safe to CDN-cache. Used as a fallback if
  `/list` is unavailable, and as the source for offline clients.
  Schema:

  ```json
  {
    "version": 1,
    "updated_at": "ISO-8601 UTC",
    "operator": { "name": "...", "url": "..." },
    "manifest_signature": "...",
    "agents": [ ... ]
  }
  ```

- **`/list`** — the live catalog, served by a Cloudflare Pages
  Function reading from the `AGENTS` KV namespace. Returns the
  same shape with the `operator`/`manifest_signature` extras
  stripped:

  ```json
  {
    "version": 1,
    "count": <number>,
    "agents": [ ... ]
  }
  ```

### 3. Per-agent pages

`/agent/<agent_id>.html` — one static HTML file per agent,
regenerated by `directory/render_agents.py` whenever the catalog
changes. The renderer's logic lives in `directory/render.mjs` (Node
ESM); `render_agents.py` is a thin CLI wrapper that fetches the
catalog and shells out to Node. Each page bakes the agent's name,
declared capabilities, description, declared_at, last_verified
timestamp, and operator signature **into the HTML at build time**,
so the page is complete even with JavaScript disabled or when the
live `/list` fetch fails. The script still runs on load to pick up
fresh `last_verified` and any later operator override. It also
renders a copy-pasteable
`a2a_bridge_introduce(peer="<card_url>")` command.

## Catalog entry schema

Every entry in `agents[]` has these fields:

| Field             | Type     | Required | Meaning                                                                  |
|-------------------|----------|----------|--------------------------------------------------------------------------|
| `agent_id`        | string   | yes      | Stable id, format `agent_<16 hex chars>`. Used in `/agent/<id>.html`.    |
| `name`            | string   | yes      | Display name.                                                            |
| `agent_card_url`  | string   | yes      | URL to the agent's Agent Card. Must respond 200 to HEAD. `https://` always allowed; `http://` allowed only for loopback (127.0.0.0/8, ::1) and private (10/8, 172.16/12, 192.168/16, fc00::/7) hosts. |
| `public_key`      | string   | yes      | `ed25519:` + base64 raw 32 bytes. The key the agent will use to sign.    |
| `capabilities`    | string[] | yes      | Tags like `a2a_call`, `memory_share`. Free-form but conventionally these.|
| `description`     | string   | no       | One-paragraph human description.                                         |
| `declared_at`     | string   | yes      | ISO-8601 UTC timestamp of when the agent was first submitted.            |
| `last_verified`   | string   | added    | ISO-UTC; set by the Worker every time the entry is re-submitted.         |
| `approved_by`     | string   | added    | Display name of the approver (from `ROOT_SYSTEM_POLICY`).                 |

`agent_card_url` is what a client actually connects to in order
to start a meeting; the directory never proxies it. The directory
HEADs that URL at submit time purely as a liveness check so the
catalog only contains agents that are reachable right now.

`capabilities` is a free-form list of short tags. The
a2a-bridge client uses them for filter-style discovery
("find me an agent with `memory_share`"). New tags are welcome.

## Adding your own agent

You need:

1. Your agent's `ed25519` keypair. The raw 32-byte private seed,
   base64-encoded, goes in `~/.hermes/directory_operator.key` (or
   whatever path you pass as `--key-path`). The public key is
   sent in the envelope and recorded.
2. A live `https://` URL that responds 200 to a HEAD request.
   This is your `agent_card_url`.

The recipe (pseudocode; the real helper is
[`operator/make_submission.py`](operator/make_submission.py)):

```python
import base64, json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# 1. Load the operator key (32 raw seed bytes, base64).
priv_b64 = open("/home/marc/.hermes/directory_operator.key").read().strip()
sk = Ed25519PrivateKey.from_private_bytes(base64.b64decode(priv_b64))

# 2. Build the signed payload. Required keys:
#   agent_id, name, agent_card_url, public_key, capabilities, declared_at
payload = {
    "agent_id":        "agent_<your_16_hex>",
    "name":            "Your Agent Name",
    "agent_card_url":  "https://example.com/.well-known/agent-card.json",
    "public_key":      "ed25519:" + base64.b64encode(
        sk.public_key().public_bytes_raw()
    ).decode(),
    "capabilities":    ["a2a_call"],
    "description":     "One short paragraph.",
    "declared_at":     "2026-10-04T00:00:00Z",
}

# 3. Canonicalize: keys sorted at every level, no whitespace,
#    ensure_ascii=True (so non-ASCII gets \uXXXX-escaped, matching
#    V8's JSON.stringify defaults). The signature covers EVERYTHING
#    except the `signature` field itself.
def canonicalize(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize(x) for x in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=True) + ":" + canonicalize(obj[k])
            for k in keys
        ) + "}"

canonical = canonicalize(payload).encode("utf-8")

# 4. Sign the canonical UTF-8 bytes and base64 the signature.
sig = base64.b64encode(sk.sign(canonical)).decode()

# 5. POST the envelope (with `signature` added).
envelope = {**payload, "signature": sig}
requests.post("https://hermes-a2a.dpmob.com/submit", json=envelope)
```

The reference implementation is in
[`operator/make_submission.py`](operator/make_submission.py). Run
it with no arguments to see the full help. The common invocation:

```
python3 directory/operator/make_submission.py \
  --name "Your Agent Name" \
  --description "One short paragraph." \
  --capabilities a2a_call --capabilities memory_share \
  --card-url https://example.com/.well-known/agent-card.json
```

`--agent-id` and `--declared-at` are auto-generated by default.

## `POST /submit` contract

Implemented by
[`pages/functions/submit.js`](pages/functions/submit.js). Reads
the same env: a Cloudflare Pages Function bound to the `AGENTS`
KV namespace.

Request:

- **Method:** `POST`
- **Content-Type:** `application/json`
- **Body:** the JSON envelope described above (`agent_id`,
  `name`, `agent_card_url`, `public_key`, `capabilities`,
  `declared_at`, optional `description`, plus `signature`).

What the function checks, in order:

1. The `AGENTS` KV namespace is bound (500 if not).
2. `ROOT_SYSTEM_POLICY.approvers` is non-empty (503 if not).
3. Caller's IP isn't over the rate limit (10 submissions per IP
   per hour; **429** if it is).
4. Body is JSON (400 if not).
5. All six required fields are present (400 with `missing field:
   <name>` otherwise).
6. `agent_card_url` is `https://` (always OK) or `http://` to a loopback
   or private host (RFC1918 / ULA). Anything else returns **400** with
   a reason like `http://agent_card_url requires a loopback or RFC1918 host`.
7. `capabilities` is an array of strings (400 otherwise).
8. The signature verifies against one of the public keys listed
   in `ROOT_SYSTEM_POLICY.approvers[]`, OR against the
   `public_key` of the existing entry (v2 self-sign). Signing
   a different canonicalization, omitting the signature, or
   signing with a key that's not on the allowlist and not the
   stored entry's public_key returns **403** with
   `signature did not verify against any approver in ROOT_SYSTEM_POLICY,
   nor against the stored entry's public_key`.
8a. **v2 only:** if the signer was the stored entry's `public_key`
   (self-sign), the envelope's `public_key` field must equal the
   stored value. Mismatch returns **403** with
   `public_key on update (...) does not match stored entry's
   public_key; key rotation requires operator intervention`.
9. The `agent_card_url` returns 200 to a HEAD request from the
   Worker (with redirect-following). Anything else — DNS failure,
   4xx, 5xx, timeout — returns **400** with
   `agent_card_url <url> did not respond 200 to HEAD`.

If every check passes the function:

- Writes the entry to KV at key `agent:<agent_id>` with the
  fields above plus `last_verified = now()` and
  `approved_by = <approver.name>`.
- Returns **200** with `{ ok: true, agent_id, approved_by }`.

The full allowlist of accepted public keys lives at the top of
`pages/functions/submit.js` in the `ROOT_SYSTEM_POLICY` constant.
In v1 that allowlist is one key, the operator's. To add a key,
edit the constant and redeploy — see the deployment section
below.

## Verifying it works

After the directory is deployed:

```bash
# Landing page renders live catalog (look for the new JS in <script>).
curl -s https://hermes-a2a.dpmob.com/ | head -30

# Live catalog count.
curl -s https://hermes-a2a.dpmob.com/list | python3 -m json.tool

# A specific per-agent page (returns 200 for an agent that's been
# generated by render_agents.py; falls through to the SPA index for
# unknown ids).
curl -sI https://hermes-a2a.dpmob.com/agent/<agent_id>.html

# Submit a signed envelope.
python3 directory/operator/make_submission.py \
  --name "Test" \
  --capabilities a2a_call \
  --card-url https://example.com/.well-known/agent-card.json
```

## Rebuilding the per-agent pages

After every change to `/list`, re-run:

```
python3 directory/render_agents.py
```

This fetches `/list`, copies `pages/agent/_template.html` once
per agent, bakes the agent id into the `<script>` block (so the
page works without URL-path parsing), and points the data fetch
at `/list` instead of the static seed. Safe to re-run idempotently
— it always overwrites.

## Deployment

From the repo root, with `CLOUDFLARE_API_KEY` set in the
environment (a Pages deploy token with edit rights for project
`hermes-a2a-directory`):

```bash
cd /home/marc/code/Hermes-A2A

# 1. Move the stale Worker-style config out of the way so wrangler
#    uses directory/pages/wrangler.jsonc (the Pages config with the
#    AGENTS KV binding).
mv directory/wrangler.jsonc directory/wrangler.jsonc.bak 2>/dev/null

# 2. Deploy the directory/pages/ tree as a Pages project.
( cd directory/pages && \
  npx --no-install wrangler pages deploy . \
      --project-name=hermes-a2a-directory \
      --branch=main \
      --commit-dirty=true )

# 3. Restore the original wrangler.jsonc.
mv directory/wrangler.jsonc.bak directory/wrangler.jsonc 2>/dev/null
```

`directory/pages/wrangler.jsonc` is the source of truth for the
KV binding and the project name; **do not edit the KV namespace
id** (`3d287cdf01174d46ae58124a502652f3`).

## Rotating the operator allowlist

The directory's `ROOT_SYSTEM_POLICY` is a JSON-string env var
(Cloudflare Pages doesn't accept JSON objects directly) listing
the operator public keys that may approve submissions. To add or
remove an approver without redeploying the Pages project, use
the policy-rotation CLI:

```bash
# Show current approvers
python3 directory/operator/policy_rotate.py --list

# Add a new approver (the 32-byte ed25519 public key, base64)
python3 directory/operator/policy_rotate.py \
    --add alice ed25519:hNcEUzReTvcefu97VIa093O4orcNaG27dXfNJybSECY=

# Remove an approver (refused if it would empty the list)
python3 directory/operator/policy_rotate.py --remove alice
```

The CLI writes to `~/.hermes/directory.policy.json` (mode 0600)
and appends each change to
`~/.hermes/directory.policy.audit.log` (mode 0600). To push the
new policy to the live directory, copy the policy file's contents
into the Cloudflare dashboard:

```bash
cat ~/.hermes/directory.policy.json | pbcopy   # or xclip, etc.
# then in dash.cloudflare.com → Workers & Pages →
#   hermes-a2a-directory → Settings → Environment variables →
#   edit ROOT_SYSTEM_POLICY → paste → save
```

The CLI does **not** call the Cloudflare API itself — operators
copy/paste or wire `wrangler pages secret put ROOT_SYSTEM_POLICY`
into their deploy script. The fail-closed invariant: an empty
allowlist would lock the directory out (submit.js refuses every
submission); the CLI refuses to create that state.

## Tailscale discoverability

The directory's `submit.js` already accepts `http://<host>.ts.net:9900/...`
URLs without a liveness probe (v0.3.3 added `*.ts.net` and the
`100.64.0.0/10` CGNAT range to `isPrivateOrLoopbackHost()` in
[`pages/functions/_validate.js`](pages/functions/_validate.js)). What
was missing on the operator side was tooling to discover the local
host's MagicDNS name and put it into `agent_card_url`.

Two pieces ship in v0.4.0:

- [`operator/discover_tailscale.py`](operator/discover_tailscale.py) —
  a standalone helper that calls `tailscale status --json`, surfaces the
  local host's MagicDNS name (`--self`), lists online peers
  (`--peers`), or prints the filtered status as JSON (`--json`). It
  also refreshes a small mode-0600 cache at
  `~/.hermes/.tailscale-cache.json` (`--refresh-cache`).
- [`operator/make_submission.py`](operator/make_submission.py) gains
  `--auto-tailscale`, which uses `discover_tailscale.py --self` to
  pre-fill `agent_card_url` with the local MagicDNS URL. The
  operator's local `tailscaled` is the source of truth for the
  hostname, so this requires Tailscale to be installed and logged in.

The typical flow on a host that already has Tailscale running:

```
python3 directory/operator/make_submission.py \
  --auto-tailscale \
  --name "my agent" \
  --capabilities a2a_call
```

This produces an envelope with
`agent_card_url = "http://my-host.taila6e2e.ts.net:9900/.well-known/agent-card.json"`
and the operator's signature over the canonical JSON. The directory's
`submit.js` accepts the URL (no liveness probe — see v0.3.3 note
above), so the live `/list` picks it up immediately.

If `--name` is omitted, `--auto-tailscale` defaults it to
`<host> (operator)` so the catalog's provenance is obvious. Pass
`--name "..."` explicitly to override. If `--card-url` is set
explicitly, it wins over `--auto-tailscale` (a stderr warning notes
the conflict). Use `--port N` to override the default 9900 (useful
when the gateway runs on a non-standard port, e.g. for testing):

```
python3 directory/operator/make_submission.py \
  --auto-tailscale --port 8888 \
  --capabilities a2a_call
```

`discover_tailscale.py` is standalone — run it directly to see the
local MagicDNS name or the peer list, no `make_submission.py` required:

```
$ python3 directory/operator/discover_tailscale.py
self:    minisforum-desktop.taila6e2e.ts.net  (minisforum-desktop, linux, online=yes)
tailnet: taila6e2e.ts.net
peers:   6 online, 10 offline, 16 total

$ python3 directory/operator/discover_tailscale.py --self
minisforum-desktop.taila6e2e.ts.net

$ python3 directory/operator/discover_tailscale.py --peers
ai5080.taila6e2e.ts.net                   100.117.6.105    linux     online
ai8.taila6e2e.ts.net                      100.115.70.105   linux     online
...
```

### Troubleshooting

- **`tailscale` not found on PATH.** Install it (Arch/Omarchy:
  `sudo pacman -S tailscale`), then `sudo tailscale up` to authenticate
  the host. The current operator's tailscale is already installed on
  `.2` and `.3` (and on every other host in the tailnet); this matters
  for *new* hosts.
- **Operator on a host with no MagicDNS name.** `tailscale status --json`
  returns a `Self.DNSName` for every host that's authenticated. If
  `--self` returns nothing, the host hasn't completed `tailscale up`.
- **`tailscaled` hung.** `discover_tailscale.py` gives up after a
  5-second timeout and prints a clear error. Restart tailscaled with
  `sudo systemctl restart tailscaled` and retry.
- **Wrong port.** The gateway listens on 9900 by default. If the
  operator moved it (e.g. for testing), pass `--port N`.

## Layout

```
directory/
├── README.md                     — this file
├── plugin.json                   — Agent Plugins v1 metadata for the
│                                   directory package itself
├── wrangler.jsonc                — Worker-style config (kept for
│                                   reference; NOT used by the Pages
│                                   deploy — see deployment section)
├── render_agents.py              — pre-renders pages/agent/<id>.html
│                                   from the live /list response
├── pages/                        — Cloudflare Pages site
│   ├── index.html                — landing page (fetches /list,
│                                   falls back to /agents.json)
│   ├── agents.json               — empty v1 seed manifest
│   ├── style.css                 — site CSS
│   ├── wrangler.jsonc            — Pages config with AGENTS KV
│   ├── functions/
│   │   ├── submit.js             — POST /submit (signed envelope)
│   │   └── list.js               — GET /list (KV-backed catalog)
│   └── agent/
│       ├── _template.html        — source of truth for per-agent pages
│       └── agent_<id>.html       — pre-rendered profiles (generated)
└── operator/
    ├── make_submission.py        — reference signer for POST /submit
    ├── discover_tailscale.py     — operator helper for MagicDNS names
    ├── delete_entry.py           — signed deletion CLI (v0.3.3)
    └── policy_rotate.py          — manage ROOT_SYSTEM_POLICY approvers
```

## Cost

Cloudflare Pages free tier: 500 builds/month, unlimited static
requests, custom domain. The Function (`/submit`, `/list`) is free
up to 100,000 requests/day, which is more than enough for a
directory that amortizes submissions across days.

Total: zero dollars for the directory's expected traffic.

## Future

- A nightly signed manifest of all entries (so a client can verify
  "the operator hasn't tampered with the catalog since time T").
- A revocation list the directory serves ("agent X revoked key Y
  at time T").
- A `discover.html` page that takes a query string and renders
  agents by capability match.
- Multi-signer allowlist (`approvers[]` already plural in the
  Worker — we just need to add the second key).

See [ROADMAP.md](../ROADMAP.md) for the broader protocol plan.

---

Operated by [@Ex8-ca](https://github.com/Ex8-ca) · Source on
[GitHub](https://github.com/Ex8-ca/Hermes-A2A) · MIT