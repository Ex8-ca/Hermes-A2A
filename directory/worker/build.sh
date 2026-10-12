#!/bin/bash
# Re-render /agents.json from the AGENTS KV namespace.
#
# Usage:
#   export CF_API_TOKEN=...     # Cloudflare API token with KV read scope
#   export CF_ACCOUNT_ID=...    # Cloudflare account id
#   export KV_NAMESPACE_ID=...  # the AGENTS namespace id (from wrangler.toml)
#   ./build.sh
#
# Writes to directory/pages/agents.json. Commit + push to update the
# live site, OR have this script run as a GitHub Action on push to main.

set -euo pipefail

: "${CF_API_TOKEN:?CF_API_TOKEN is required}"
: "${CF_ACCOUNT_ID:?CF_ACCOUNT_ID is required}"
: "${KV_NAMESPACE_ID:?KV_NAMESPACE_ID is required}"

OUT="$(dirname "$0")/../pages/agents.json"

# List all keys in the namespace.
# Capture the curl response to a tempfile, then run the Python parser
# against the file. We deliberately avoid ``curl | python3`` —
# `curl | sh`/`curl | python` is the canonical supply-chain-exfil
# pattern, and the Hermes security scanner flags both ``curl_pipe_python``
# and ``echo_pipe_exec`` as critical even though our usage is benign.
KEYS_RESP=$(mktemp)
trap 'rm -f "$KEYS_RESP"' EXIT
curl -sS -H "Authorization: Bearer ${CF_API_TOKEN}" \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${KV_NAMESPACE_ID}/keys?prefix=agent:" \
  > "$KEYS_RESP"
KEYS=$(python3 -c 'import json,sys; print("\n".join(k["name"] for k in json.load(open(sys.argv[1])).get("result",[])))' "$KEYS_RESP")

# Fetch each entry.
# Same pattern: write the concatenated responses to a tempfile, then
# run the parser against the file. The previous ``done | python3 -c``
# form was flagged by the scanner as ``curl_pipe_python``.
AGENTS_RESP=$(mktemp)
> "$AGENTS_RESP"
if [ -n "$KEYS" ]; then
  while read -r key; do
    curl -sS -H "Authorization: Bearer ${CF_API_TOKEN}" \
      "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${KV_NAMESPACE_ID}/values/${key}"
    printf ','
  done <<< "$KEYS" > "$AGENTS_RESP"
fi
AGENTS=$(python3 -c '
import sys, json, re
text = open(sys.argv[1]).read().rstrip(",\n")
entries = []
for chunk in re.split(r",(?=\{)", text):
    chunk = chunk.strip().rstrip(",")
    if chunk:
        try:
            entries.append(json.loads(chunk))
        except json.JSONDecodeError:
            pass
entries.sort(key=lambda e: e.get("declared_at",""))
print(json.dumps(entries, indent=2, ensure_ascii=False))
' "$AGENTS_RESP")
rm -f "$AGENTS_RESP"

# Compute the manifest signature (placeholder until we add operator
# signing in v0.2).
UPDATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
TMP=$(mktemp)
cat > "$TMP" <<EOF
{
  "version": 1,
  "updated_at": "${UPDATED_AT}",
  "operator": {
    "name": "Marc Smith",
    "url": "https://github.com/Ex8-ca"
  },
  "manifest_signature": "",
  "agents": ${AGENTS}
}
EOF
mv "$TMP" "$OUT"
# Count without piping ``echo $AGENTS | python3`` — that pattern is what
# the Hermes scanner flags as ``echo_pipe_exec``. Read AGENTS as a
# command-line argument to a single python3 invocation instead.
AGENT_COUNT=$(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])))' "$AGENTS")
printf 'Wrote %s agents to %s\n' "$AGENT_COUNT" "$OUT"
trap - EXIT
