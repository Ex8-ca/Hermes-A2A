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
KEYS=$(curl -sS -H "Authorization: Bearer ${CF_API_TOKEN}" \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${KV_NAMESPACE_ID}/keys?prefix=agent:" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(k['name'] for k in d.get('result',[])))")

# Fetch each entry.
AGENTS="[]"
if [ -n "$KEYS" ]; then
  AGENTS=$(echo "$KEYS" | while read -r key; do
    curl -sS -H "Authorization: Bearer ${CF_API_TOKEN}" \
      "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${KV_NAMESPACE_ID}/values/${key}"
    echo ","
  done | python3 -c "
import sys, json
parts = sys.stdin.read().rstrip(',\n')
# parts is a sequence of '<json>,' tokens. Strip trailing commas and
# wrap in a JSON array.
import re
entries = []
for chunk in re.split(r',(?=\{)', parts):
    chunk = chunk.strip().rstrip(',')
    if chunk:
        try:
            entries.append(json.loads(chunk))
        except json.JSONDecodeError:
            pass
entries.sort(key=lambda e: e.get('declared_at',''))
print(json.dumps(entries, indent=2, ensure_ascii=False))
")
fi

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
echo "Wrote $(echo "$AGENTS" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))") agents to $OUT"
