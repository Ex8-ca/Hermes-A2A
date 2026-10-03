# DEPLOY_STATUS — `hermes-a2a.dpmob.com`

Last updated: 2026-10-03

## TL;DR

The directory is **live and operational** at `https://hermes-a2a.dpmob.com/`.

| Component | Status |
| --- | --- |
| Static landing page (`/`) | ✅ 200, served by Cloudflare Pages |
| Static catalog (`/agents.json`) | ✅ 200, served by Cloudflare Pages |
| Submit endpoint (`POST /submit`) | ✅ 200, Pages Function, signature-verified |
| List endpoint (`GET /list`) | ✅ 200, Pages Function, reads AGENTS KV |
| Custom domain + HTTPS | ✅ Google Trust Services WE1 cert, valid through 2027-01-01 |
| HSTS | ✅ 1-year, includeSubDomains, always-HTTPS on |
| Operator keypair | ✅ `~/.hermes/directory_operator.key` (priv, 0600), pub embedded in `submit.js` |
| AGENTS KV namespace | ✅ `3d287cdf01174d46ae58124a502652f3` |
| Pages project | ✅ `hermes-a2a-directory` (id `1ece728b-e8f1-4344-8df7-96d5e260d5e3`) |

## Live end-to-end (verified 2026-10-03T17:30Z)

```
GET  /               → 200  text/html                  (static)
GET  /agents.json    → 200  application/json          (static, empty catalog seed)
GET  /list           → 200  application/json          (function, reads AGENTS KV)
GET  /submit         → 200  text/html                  (function, 405 on POST without sig)
POST /submit         → 200  {"ok":true,"agent_id":"agent_83c2d59a6c821f7b","approved_by":"Marc Smith (operator)"}
```

A signed submission round-trip succeeds, the entry persists in KV, and `/list` returns it.

## Gotchas we hit (so future-us doesn't repeat them)

### 1. Read-only DNS records managed by a deleted Worker
A previously-deployed `directory` Worker (no longer present) left an AAAA record
(`hermes-a2a.dpmob.com → 100::`) that Cloudflare marked as `read_only: True` with
`origin_worker_id` set to the (now-deleted) worker. The API rejected every
attempt to delete or edit it: `400: Unable to edit this record as this has been
configured as read only.` Even after deleting the worker, the record remained.

**Fix:** Re-create the Pages project and the custom-domain binding in a single
sweep. The `directory` worker was somehow linked to the `hermes-a2a-directory`
Pages project, and deleting the worker also deleted the Pages project — which
cleared the orphaned DNS record. We then re-created the Pages project, re-attached
the custom domain, and added an explicit CNAME.

**If you see "managed by Workers" again:** the orphaned record is back. The
dashboard's "Delete" option on DNS records can sometimes force-delete what the
API cannot. If the dashboard also blocks, file a Cloudflare support ticket
asking them to delete the orphaned record by `record_id` and `origin_worker_id`.

### 2. Pages Functions don't get re-bound to a custom domain on re-add
After deleting and re-adding the custom domain, the static site serves fine
but the functions 404. The function bundle uploaded cleanly to the project, but
the custom-domain → function routing requires a fresh deploy. The function also
doesn't appear in the deploy output unless the deploy command is run from the
directory containing the `functions/` folder.

**Fix:** Always run `wrangler pages deploy` from `directory/pages/`, never
from `directory/`. The function bundle only gets uploaded when wrangler sees
`functions/` in the current working directory.

### 3. The "Dashboard re-attach" dance has lag
The Cloudflare dashboard's "Set up a custom domain" flow takes ~1-3 minutes to
propagate cert + verification. During that window, `https://<host>/` returns
522 (connection timeout) and `verification_data.status` is `pending` even
though the cert is being issued. Don't panic — wait at least 5 minutes before
debugging.

### 4. The wrangler.jsonc at `directory/wrangler.jsonc` is a trap
There's a legacy `wrangler.jsonc` at `directory/` from an earlier `wrangler
dev` session. It treats the project as a **Worker** (`has_modules: true,
has_assets: true`, `name: "directory"`), not as the **Pages** project. If
wrangler picks it up, it will deploy to the wrong project, ignore `functions/`,
and may try to bind the wrong KV namespace.

**Always move it out of the way before deploying:**
```bash
mv directory/wrangler.jsonc directory/wrangler.jsonc.bak
# ...wrangler pages deploy...
mv directory/wrangler.jsonc.bak directory/wrangler.jsonc
```

The correct config is at `directory/pages/wrangler.jsonc` (name
`hermes-a2a-directory`, no `assets.directory`).

### 5. Browser caches the "not secure" state
After the cert was provisioned, Chrome's regular profile still showed
"Not secure" for `hermes-a2a.dpmob.com/`, even though the cert was valid. This
is browser-side cached state from when the cert chain was broken during the
debugging. **Incognito mode worked fine.** Clearing the per-host HSTS state
fixes it: `chrome://net-internals/#hsts` → delete `hermes-a2a.dpmob.com`.

## How to redeploy from scratch

If everything is wiped and you need to bring the directory back up from
nothing, run the scripts in `directory/scripts/`:

```bash
cd /home/marc/code/Hermes-A2A

# 1. (re-)create the operator keypair if it doesn't exist
if [ ! -f ~/.hermes/directory_operator.key ]; then
  uv run python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import base64
sk = Ed25519PrivateKey.generate()
priv = sk.private_bytes(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw, encryption_algorithm=serialization.NoEncryption())
print(base64.b64encode(priv).decode())
" > ~/.hermes/directory_operator.key
  chmod 600 ~/.hermes/directory_operator.key
  echo "New operator keypair generated"
fi

# 2. Move the legacy wrangler config out of the way
mv directory/wrangler.jsonc directory/wrangler.jsonc.bak 2>/dev/null

# 3. Deploy
cd directory/pages
npx --no-install wrangler pages deploy . --project-name=hermes-a2a-directory --branch=main --commit-dirty=true
cd ../..

# 4. Re-add the custom domain
#    This is the only step that needs the dashboard UI — see "Re-attaching the
#    custom domain" below.

# 5. Restore the legacy config (kept in case the user wants it back)
mv directory/wrangler.jsonc.bak directory/wrangler.jsonc 2>/dev/null
```

### Re-attaching the custom domain via the API

```python
# Use the same Cloudflare API token, with Zones:Edit, Pages:Edit perms
# (token stored in CLOUDFLARE_API_KEY env var)

import os, json, urllib.request
TOKEN = os.environ["CLOUDFLARE_API_KEY"]
ACCOUNT = "5d1cd9bd2ba540685d50e93ff83e8d23"
ZONE_ID = "92c696d2415bd45c141fd4ffce20ca37"

def req(path, method="GET", body=None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"https://api.cloudflare.com/client/v4{path}",
        data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.status, json.loads(resp.read())

# Create the project (if it doesn't exist)
req(f"/accounts/{ACCOUNT}/pages/projects", method="POST", body={
    "name": "hermes-a2a-directory",
    "production_branch": "main",
})

# Create the AGENTS KV namespace (if it doesn't exist) and update wrangler.jsonc
ns = req(f"/accounts/{ACCOUNT}/storage/kv/namespaces", method="POST", body={"title": "AGENTS"})
[1]  # not always 200 if it already exists, but if 400 with 10014, the existing namespace is fine
# Find the id by listing

# Add the custom domain
req(f"/accounts/{ACCOUNT}/pages/projects/hermes-a2a-directory/domains", method="POST",
    body={"name": "hermes-a2a.dpmob.com"})

# Add the CNAME record (proxied, ttl=1)
req(f"/zones/{ZONE_ID}/dns_records", method="POST", body={
    "type": "CNAME",
    "name": "hermes-a2a.dpmob.com",
    "content": "hermes-a2a-directory.pages.dev",
    "proxied": True,
    "ttl": 1,
})
```

Wait ~2 minutes for cert provisioning and verification. Then test:
```bash
curl -I https://hermes-a2a.dpmob.com/   # expect 200
curl    https://hermes-a2a.dpmob.com/list
```

## File map

| Path | Purpose |
| --- | --- |
| `directory/pages/index.html` | Static landing page |
| `directory/pages/style.css` | Styles |
| `directory/pages/agents.json` | Static empty catalog (seed) |
| `directory/pages/agent/_template.html` | Per-agent profile template (unreferenced for now) |
| `directory/pages/functions/submit.js` | Pages Function: signed submissions |
| `directory/pages/functions/list.js` | Pages Function: catalog reader |
| `directory/pages/wrangler.jsonc` | Pages project config + KV binding |
| `directory/worker/` | Legacy Cloudflare Worker (kept for reference; not deployed) |
| `~/.hermes/directory_operator.key` | Operator's ed25519 private key, mode 0600 |
| `directory/README.md` | High-level overview of the directory |

## Known limitations

- **`index.html` doesn't read from the function's `/list`.** It shows the static
  `agents.json` (which is empty). To see live submissions, visit
  `https://hermes-a2a.dpmob.com/list` directly. A future iteration could have
  the page JS fetch `/list` and render the agents dynamically.

- **No UI for submission.** Right now submission is `POST /submit` with a
  manually-built signed envelope. A future iteration could add a
  "Register your agent" form on the index page.

- **One operator.** `submit.js` has a single pubkey allowlist. Adding more
  operators requires editing the `approvers` array and re-deploying.

- **No rate limiting beyond the operator's signature.** A malicious operator
  could flood the KV. The signature gate keeps out random submissions, but
  doesn't rate-limit the operator themselves. Future: add a per-operator
  submission counter with a daily cap.

- **DNS record for the AAAA legacy is gone but the old AAAA → `100::` may
  return if the Worker is re-deployed.** If you re-deploy the legacy Worker,
  re-delete the Pages project's custom domain first.

## Operator workflow

To sign a new submission envelope (from the host that has the operator key):

```python
import json, base64, hashlib
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

priv_b64 = open("/home/marc/.hermes/directory_operator.key").read().strip()
sk = Ed25519PrivateKey.from_private_bytes(base64.b64decode(priv_b64))
pub = sk.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
pub_b64 = base64.b64encode(pub).decode()
agent_id = "agent_" + hashlib.sha256(pub).digest()[:8].hex()

envelope = {
    "agent_id": agent_id,
    "name": "Your bot's display name",
    "agent_card_url": "https://your-host.example.com/.well-known/agent-card.json",
    "public_key": "ed25519:" + pub_b64,
    "capabilities": ["a2a_call", "memory_share"],
    "description": "What this agent does.",
    "declared_at": "2026-10-03T17:30:00Z",
}

def canonicalize(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize(x) for x in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonicalize(obj[k]) for k in keys) + "}"

canon = canonicalize(envelope).encode("utf-8")
envelope["signature"] = base64.b64encode(sk.sign(canon)).decode()

# POST to /submit
import urllib.request
req = urllib.request.Request("https://hermes-a2a.dpmob.com/submit",
    data=json.dumps(envelope).encode(),
    headers={"Content-Type": "application/json"},
    method="POST")
with urllib.request.urlopen(req, timeout=10) as resp:
    print(json.loads(resp.read()))
```

## Security notes

- The `cfat_` API token used for this deployment is now permanently in the
  conversation log. **Rotate it:** Cloudflare dashboard → My Profile → API
  Tokens → click the token → Roll. The new value goes in
  `~/.hermes/.env` as `CLOUDFLARE_API_KEY`.

- The operator's public key is embedded in the deployed function
  (`directory/pages/functions/submit.js`). This is **not** a secret — the
  public half of an ed25519 keypair is published by design so the directory
  can verify signatures. The private key at
  `~/.hermes/directory_operator.key` is the only secret. It must never be
  committed or pasted into chat.

- The AGENTS KV namespace has no per-key access controls beyond the
  Pages-function-bound access token. Anyone with the operator's signature
  can read all stored entries.
