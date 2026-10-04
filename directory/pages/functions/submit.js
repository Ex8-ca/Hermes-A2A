// Pages Function: /submit
//
// Accepts signed agent directory submissions. Verifies the
// signature against the operator's allowlist (ROOT_SYSTEM_POLICY)
// and writes the entry into the AGENTS KV namespace.
//
// Wire format: see directory/worker/README.md for the signed-envelope schema.

import { validateAgentCardUrl } from "./_validate.js";
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

  const { signature, ...signed } = body;
  const canonical = canonicalize(signed);
  const messageBytes = new TextEncoder().encode(canonical);

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
