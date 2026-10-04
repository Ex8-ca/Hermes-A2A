// Pages Function: /submit
//
// Accepts signed agent directory submissions. Verifies the
// signature against the operator's allowlist (ROOT_SYSTEM_POLICY)
// and writes the entry into the AGENTS KV namespace.
//
// Wire format: see directory/worker/README.md for the signed-envelope schema.

import { validateAgentCardUrl, isPrivateOrLoopbackHost, classifyTransport } from "./_validate.js";
import { canonicalize } from "./canonicalize.js";

// v1: hardcoded single-operator allowlist.
// v2: read from env.ROOT_SYSTEM_POLICY (JSON) when set, with a hardcoded
//     fallback for backward compatibility. Multi-operator support lives
//     in the JSON; see directory/README.md for the schema.
const DEFAULT_ROOT_SYSTEM_POLICY = {
  version: 1,
  approvers: [
    {
      name: "Marc Smith (operator)",
      public_key: "ed25519:qebTERZot2aZKIDT4VF7hub0llZFlJwpFGu7EvZ/YYk=",
    },
  ],
};

function loadRootSystemPolicy(env) {
  if (env && env.ROOT_SYSTEM_POLICY) {
    try {
      const parsed = typeof env.ROOT_SYSTEM_POLICY === "string"
        ? JSON.parse(env.ROOT_SYSTEM_POLICY)
        : env.ROOT_SYSTEM_POLICY;
      // Distinguish: env var present but invalid → fall back to default;
      // env var present and valid (even with empty approvers) → use as-is
      // (caller will get a 503 if approvers is empty).
      if (parsed && Array.isArray(parsed.approvers)) {
        return parsed;
      }
    } catch {
      // fall through to default
    }
  }
  return DEFAULT_ROOT_SYSTEM_POLICY;
}

const ALG = { name: "Ed25519", namedCurve: "Ed25519" };
const KV_BINDING = "AGENTS";

function isPrivateOrLoopbackUrl(url) {
  // Thin URL-wrapping shim over the host-level helper in _validate.js.
  // Keeps the URL parsing in one place and lets us add new private
  // ranges (e.g. *.ts.net) by editing one function.
  try {
    const u = new URL(url);
    return isPrivateOrLoopbackHost(u.hostname);
  } catch {
    return false;
  }
}

function jsonResponse(status, body, extraHeaders = {}) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "POST, GET, OPTIONS",
      "access-control-allow-headers": "content-type",
      ...extraHeaders,
    },
  });
}

function bad(status, message, extra = {}) {
  return jsonResponse(status, { error: message, ...extra });
}

function b64ToBytes(b64) {
  const cleaned = b64
    .replace(/^ed25519:/, "")
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const padded = cleaned + "===".slice((cleaned.length + 3) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function importEd25519PublicKey(raw32) {
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

const RATE_PER_HOUR = 10;
const rateBuckets = new Map();

function checkRate(ip) {
  const hour = Math.floor(Date.now() / 3600000);
  const cur = rateBuckets.get(ip);
  if (!cur || cur.hour !== hour) {
    rateBuckets.set(ip, { count: 1, hour });
    return true;
  }
  if (cur.count >= RATE_PER_HOUR) return false;
  cur.count++;
  return true;
}

function clientIp(request) {
  return (
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "unknown"
  );
}

async function handleSubmit(request, env) {
  const ROOT_SYSTEM_POLICY = loadRootSystemPolicy(env);
  if (!env[KV_BINDING]) {
    return bad(500, `${KV_BINDING} KV namespace not bound`);
  }
  if (ROOT_SYSTEM_POLICY.approvers.length === 0) {
    return bad(503, "ROOT_SYSTEM_POLICY has no approvers");
  }

  if (!checkRate(clientIp(request))) {
    return bad(429, "rate-limited: 10 submissions per IP per hour");
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

  const required = [
    "agent_id",
    "name",
    "agent_card_url",
    "public_key",
    "capabilities",
    "declared_at",
  ];
  for (const k of required) {
    if (!(k in body)) return bad(400, `missing field: ${k}`);
  }
  if (typeof body.agent_card_url !== "string") {
    return bad(400, "agent_card_url must be a string");
  }
  const urlCheck = validateAgentCardUrl(body.agent_card_url);
  if (!urlCheck.ok) {
    return bad(400, urlCheck.reason);
  }
  if (!Array.isArray(body.capabilities)) {
    return bad(400, "capabilities must be an array of strings");
  }

  // v0.4.1: derive the entry's transport from the agent_card_url
  // (operator-side tooling doesn't set it). A manual `transport`
  // field in the body overrides the inference — useful for
  // backward compat with v0.3.x operators who wrote entries by
  // hand, or for testing. Manual values must be in the allowlist.
  const TRANSPORT_ALLOWLIST = ["tailscale-magicdns", "lan", "https", "http-public"];
  let transport;
  if (body.transport != null) {
    if (!TRANSPORT_ALLOWLIST.includes(body.transport)) {
      return bad(
        400,
        `transport ${body.transport} not in the allowlist (${TRANSPORT_ALLOWLIST.join(", ")})`,
      );
    }
    transport = body.transport;
  } else {
    transport = classifyTransport(body.agent_card_url) || "https";
  }

  const { signature, ...signed } = body;
  const canonical = canonicalize(signed);
  const messageBytes = new TextEncoder().encode(canonical);

  // v1: any operator in ROOT_SYSTEM_POLICY can sign.
  // v2: if the agent_id already has an entry, the agent's own stored
  //     public_key can also sign an update. The signature must match
  //     the stored public_key (immutability — see the check below).
  //     The agent's "approved_by" in this case is the agent_id itself,
  //     since the agent is the one attesting.
  const existingRaw = await env[KV_BINDING].get(`agent:${body.agent_id}`);
  const existing = existingRaw ? JSON.parse(existingRaw) : null;

  let approvedBy = null;
  // Try operator signatures first.
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
  // v2: fall back to the agent's own stored public_key (self-sign).
  if (!approvedBy && existing && existing.public_key) {
    let ok = false;
    try {
      ok = await verifySignature(existing.public_key, messageBytes, signature);
    } catch (e) {
      // fall through
    }
    if (ok) {
      // v2 immutability: the public_key field of an update MUST match
      // the stored entry. Otherwise an attacker who stole the
      // signature capability could pivot the entry to a new key
      // they control. The agent_id stays fixed; the public_key stays
      // fixed. To rotate a key, the operator must submit a fresh entry
      // (which is currently a delete-and-recreate — see v2.1 roadmap).
      if (existing.public_key !== body.public_key) {
        return bad(
          403,
          `public_key on update (${body.public_key}) does not match stored entry's public_key; key rotation requires operator intervention`,
        );
      }
      approvedBy = { name: `self:${body.agent_id}` };
    }
  }
  if (!approvedBy) {
    return bad(403, "signature did not verify against any approver in ROOT_SYSTEM_POLICY, nor against the stored entry's public_key");
  }

  let headOk = false;
  let headStatus = 0;
  // URLs that point at private network ranges (RFC1918, Tailscale
  // 100.64.0.0/10, link-local, loopback) and Tailscale MagicDNS
  // hostnames (*.ts.net) cannot be probed by the directory's
  // Cloudflare Pages Functions — Cloudflare's edge is not on the
  // operator's private network. Skip the liveness check for these
  // URLs; the operator is responsible for the URL being correct.
  // The directory is a discovery layer, not a reachability oracle
  // — discoverers do their own reachability check when they call.
  if (isPrivateOrLoopbackUrl(body.agent_card_url)) {
    headOk = true;
  } else {
    try {
      const head = await fetch(body.agent_card_url, { method: "HEAD", redirect: "follow" });
      headOk = head.ok;
      headStatus = head.status;
    } catch {
      headOk = false;
    }
    if (!headOk) {
      // HEAD liveness probe failed. Some HTTP servers (e.g. Python's
      // BaseHTTPRequestHandler) return 501 Unsupported Method ('HEAD')
      // without implementing HEAD. Fall back to GET: a 2xx on GET is
      // just as good a proof that the agent_card_url resolves. We
      // only fall back when HEAD explicitly returned a "method not
      // allowed" status (405/501) — actual network errors still
      // refuse the submission.
      if (headStatus === 405 || headStatus === 501) {
        try {
          const get = await fetch(body.agent_card_url, { method: "GET", redirect: "follow" });
          headOk = get.ok;
        } catch {
          headOk = false;
        }
      }
    }
  }
  if (!headOk) {
    return bad(400, `agent_card_url ${body.agent_card_url} did not respond 2xx to HEAD or GET`);
  }

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
    transport,
  };
  await env[KV_BINDING].put(`agent:${body.agent_id}`, JSON.stringify(entry));

  return jsonResponse(200, {
    ok: true,
    agent_id: body.agent_id,
    approved_by: approvedBy.name,
  });
}

export async function onRequestPost(context) {
  return handleSubmit(context.request, context.env);
}

export async function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "POST, GET, OPTIONS",
      "access-control-allow-headers": "content-type",
    },
  });
}
