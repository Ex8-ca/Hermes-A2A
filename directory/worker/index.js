// Cloudflare Worker for hermes-a2a.dpmob.com/submit
//
// Accepts signed submission envelopes for the agent directory.
// Verifies the ed25519 signature against the operator's allowlist
// (ROOT_SYSTEM_POLICY) and writes the entry into a KV namespace.
//
// Deploy:  npx wrangler deploy
// Local:    npx wrangler dev

// ─────────────────────────────────────────────────────────────────────────
// ROOT_SYSTEM_POLICY — operator-controlled allowlist of approver public
// keys. Update this in the Worker source and redeploy to add/remove
// approvers. There is no runtime mutation; rotation = redeploy.
// ─────────────────────────────────────────────────────────────────────────

const ROOT_SYSTEM_POLICY = {
  version: 1,
  approvers: [
    // The operator's own ed25519 public key (32 raw bytes, base64-encoded).
    // Generate with:
    //   openssl genpkey -algorithm ed25519 -out key.pem
    //   openssl pkey -in key.pem -pubout -outform DER | tail -c 32 | base64
    // Or use the helper in the plugin: a2a_bridge_identity_pubkey()
    //
    // Example placeholder — replace before deploying:
    // { name: "Marc Smith", public_key: "ed25519:REPLACE_ME_BASE64==" }
  ],
};

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

const ALG = { name: "Ed25519", namedCurve: "Ed25519" };

function jsonResponse(status, body, extraHeaders = {}) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "POST, OPTIONS",
      "access-control-allow-headers": "content-type",
      ...extraHeaders,
    },
  });
}

function bad(status, message, extra = {}) {
  return jsonResponse(status, { error: message, ...extra });
}

function canonicalize(obj) {
  // Stable JSON: sort keys at every level. Both signer and verifier
  // MUST use the same algorithm — we pick this one for v0.1.
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) {
    return "[" + obj.map(canonicalize).join(",") + "]";
  }
  const keys = Object.keys(obj).sort();
  return (
    "{" +
    keys.map((k) => JSON.stringify(k) + ":" + canonicalize(obj[k])).join(",") +
    "}"
  );
}

function b64ToBytes(b64) {
  // Strip an optional "ed25519:" prefix; tolerate URL-safe base64.
  const cleaned = b64.replace(/^ed25519:/, "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = cleaned + "===".slice((cleaned.length + 3) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

async function importEd25519PublicKey(raw32) {
  // SPKI prefix for ed25519 public keys (DER). The raw 32 bytes
  // are wrapped into a SubjectPublicKeyInfo structure.
  const SPKI_PREFIX = new Uint8Array([
    0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00,
  ]);
  const spki = new Uint8Array(SPKI_PREFIX.length + raw32.length);
  spki.set(SPKI_PREFIX, 0);
  spki.set(raw32, SPKI_PREFIX.length);
  return crypto.subtle.importKey("spki", spki, ALG, false, ["verify"]);
}

async function verifySignature(publicKeyB64, messageBytes, signatureB64) {
  const raw = b64ToBytes(publicKeyB64);
  if (raw.length !== 32) return false;
  const sig = b64ToBytes(signatureB64);
  if (sig.length !== 64) return false;
  const key = await importEd25519PublicKey(raw);
  return crypto.subtle.verify(ALG, key, sig, messageBytes);
}

function clientIp(request) {
  return (
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "unknown"
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Rate limit (per-IP-per-hour, in-memory)
// ─────────────────────────────────────────────────────────────────────────

const RATE_PER_HOUR = 10;
const _rateBuckets = new Map(); // ip -> { count, hour }

function checkRate(ip) {
  const hour = Math.floor(Date.now() / 3600000);
  const cur = _rateBuckets.get(ip);
  if (!cur || cur.hour !== hour) {
    _rateBuckets.set(ip, { count: 1, hour });
    return true;
  }
  if (cur.count >= RATE_PER_HOUR) return false;
  cur.count++;
  return true;
}

// ─────────────────────────────────────────────────────────────────────────
// Handlers
// ─────────────────────────────────────────────────────────────────────────

async function handleSubmit(request, env) {
  if (!checkRate(clientIp(request))) {
    return bad(429, "rate-limited: 10 submissions per IP per hour");
  }
  if (!env.AGENTS) {
    return bad(500, "AGENTS KV namespace not bound");
  }
  if (ROOT_SYSTEM_POLICY.approvers.length === 0) {
    return bad(503, "ROOT_SYSTEM_POLICY has no approvers");
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return bad(400, "body must be JSON");
  }
  if (!body || typeof body !== "object") {
    return bad(400, "body must be a JSON object");
  }
  const required = ["agent_id", "name", "agent_card_url", "public_key", "capabilities", "declared_at"];
  for (const k of required) {
    if (!(k in body)) return bad(400, `missing field: ${k}`);
  }
  if (typeof body.agent_card_url !== "string" || !body.agent_card_url.startsWith("https://")) {
    return bad(400, "agent_card_url must be an https:// URL");
  }
  if (!Array.isArray(body.capabilities)) {
    return bad(400, "capabilities must be an array of strings");
  }
  const { signature, ...signed } = body;

  // Canonical bytes (keys sorted, signature excluded).
  const canonical = canonicalize(signed);
  const messageBytes = new TextEncoder().encode(canonical);

  // Verify against every approver key, first match wins.
  let approvedBy = null;
  for (const approver of ROOT_SYSTEM_POLICY.approvers) {
    let ok = false;
    try {
      ok = await verifySignature(approver.public_key, messageBytes, signature);
    } catch (e) {
      continue;
    }
    if (ok) {
      approvedBy = approver;
      break;
    }
  }
  if (!approvedBy) {
    return bad(403, "signature did not verify against any approver in ROOT_SYSTEM_POLICY");
  }

  // HEAD the agent_card_url. The card must actually be live.
  let headOk = false;
  try {
    const head = await fetch(body.agent_card_url, { method: "HEAD", redirect: "follow" });
    headOk = head.ok;
  } catch {
    headOk = false;
  }
  if (!headOk) {
    return bad(400, `agent_card_url ${body.agent_card_url} did not respond 200 to HEAD`);
  }

  // Persist.
  const entry = {
    agent_id: body.agent_id,
    name: body.name,
    agent_card_url: body.agent_card_url,
    public_key: body.public_key,
    capabilities: body.capabilities,
    description: body.description || "",
    declared_at: body.declared_at,
    last_verified: new Date().toISOString(),
    approved_by: approvedBy.name,
  };
  await env.AGENTS.put(`agent:${body.agent_id}`, JSON.stringify(entry));

  // Trigger rebuild (Pages hook). For v0.1 we just signal; the
  // operator runs build.sh or the GH Action on push.
  return jsonResponse(200, {
    ok: true,
    agent_id: body.agent_id,
    approved_by: approvedBy.name,
    next_step: "operator will run build.sh to re-render /agents.json",
  });
}

async function handleList(request, env) {
  if (!env.AGENTS) return bad(500, "AGENTS KV namespace not bound");
  const list = await env.AGENTS.list({ prefix: "agent:" });
  const agents = [];
  for (const key of list.keys) {
    const raw = await env.AGENTS.get(key.name);
    if (raw) {
      try {
        agents.push(JSON.parse(raw));
      } catch {}
    }
  }
  return jsonResponse(200, { version: 1, count: agents.length, agents });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "POST, GET, OPTIONS",
          "access-control-allow-headers": "content-type",
        },
      });
    }
    if (url.pathname === "/submit" && request.method === "POST") {
      return handleSubmit(request, env);
    }
    if (url.pathname === "/list" && request.method === "GET") {
      return handleList(request, env);
    }
    return bad(404, "not found");
  },
};
