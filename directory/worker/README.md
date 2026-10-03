# Cloudflare Worker for hermes-a2a.dpmob.com/submit

The directory has a static site (served by Cloudflare Pages) and a
**Worker** at `/submit` that accepts signed submission envelopes.

## Wire format

```
POST /submit
Content-Type: application/json

{
  "agent_id": "agent_8f3a7c2d9b1e4f5a",
  "name": "My Agent",
  "agent_card_url": "https://my-host.example.com:9900",
  "public_key": "ed25519:<base64 32 bytes>",
  "capabilities": ["a2a_call", "memory_share"],
  "description": "Optional one-line description.",
  "declared_at": "2026-10-04T00:00:00Z",
  "signature": "<base64 ed25519 signature over the canonical JSON of all fields EXCEPT signature>"
}
```

The canonical JSON is `JSON.stringify(payload_minus_signature)` with
keys in the order listed above (or alphabetical — both work as long
as the signer and verifier agree).

## What the Worker does

1. Parse the JSON body.
2. Drop the `signature` field, recompute the canonical bytes.
3. Verify the signature against every key in `ROOT_SYSTEM_POLICY`'s
   `approvers` array. First match wins.
4. Reject if no key matches (HTTP 403, structured error).
5. HEAD the `agent_card_url`. If it doesn't 200, reject (HTTP 400).
6. Push the entry into a Cloudflare KV namespace (`AGENTS`) at key
   `agent:<agent_id>`, value = the full entry (with `last_verified`
   set to now).
7. Update the `index` key to point at the new full list.
8. Re-render `/agents.json` by reading the KV list and writing to a
   Pages deployment (this is a build step; for v0.1 the operator
   runs it manually, see `build.sh`).
9. Return 200 with the new entry.

## Rate limits

- 10 submissions per IP per hour.
- 100 per `agent_id` per day.

`KV` namespace `AGENTS` is configured at the Pages project level. The
Worker is bound to the same namespace via `[[kv_namespaces]]` in
`wrangler.toml`.

## ROOT_SYSTEM_POLICY

`ROOT_SYSTEM_POLICY` is a JSON object embedded in the Worker source
at build time. Example:

```json
{
  "version": 1,
  "approvers": [
    {
      "name": "Marc Smith",
      "public_key": "ed25519:<base64>"
    }
  ]
}
```

The operator updates this file in the Worker source, commits to the
repo, and redeploys when adding a new approver. There is no runtime
mutation; rotation requires a redeploy.

## Local development

```
cd worker
npm install
npx wrangler dev
```

Wrangler will start a local dev server. The KV namespace will be
ephemeral (in-memory). To point at production KV, use
`npx wrangler tail` after deploying.

## Deployment

```
cd worker
npx wrangler deploy
```

The Worker is bound to `hermes-a2a.dpmob.com/submit` via a route in
`wrangler.toml`.

## Files

```
worker/
├── README.md
├── index.js
├── wrangler.toml
├── package.json
├── build.sh          — re-renders /agents.json from KV
└── ROOT_SYSTEM_POLICY.example.json
```

## Why manual re-render for v0.1

Cloudflare Workers + KV are great for the **write path** but the
static `/agents.json` needs a Pages deployment to update. For v0.1
we run `build.sh` from a scheduled cron (or a GitHub Action on push
to the repo) to re-render. v0.2 could use Cloudflare Workers KV
Reads in a Pages Function to render the JSON on-demand, eliminating
the rebuild step.