// Pages Function: /delete
//
// Accepts signed agent directory DELETION envelopes. Mirrors submit.js:
// verifies a signature against either the operator allowlist
// (ROOT_SYSTEM_POLICY) or the stored entry's own public_key (self-delete),
// then removes the entry from the AGENTS KV namespace and writes a
// deletion marker under a separate key prefix for audit purposes.
//
// Wire format (deletion envelope, distinct from the submit envelope kind):
//
//   {
//       "kind": "agent_deletion",     // fixed envelope kind
//       "agent_id": "agent_<16hex>" or "desktop_2" (the entry to delete),
//       "public_key": "ed25519:..."    // operator's or the entry's own key,
//       "submitted_at": "ISO-8601Z",   // UTC timestamp of the request,
//       "signature": "base64(ed25519 over canonical(envelope minus signature))"
//   }
//
// Authorization:
//   1. Signature must verify against the envelope's `public_key`.
//   2. The envelope's `public_key` must EITHER:
//        (a) match one of `ROOT_SYSTEM_POLICY.approvers[].public_key`
//            (any entry can be deleted), OR
//        (b) match the `public_key` of the EXISTING KV entry at
//            `agent:<agent_id>` (self-delete — an agent can only delete
//            its own entry).
//   3. Otherwise 403.
//
// KV layout:
//   - `agent:<agent_id>`       — the live entry (deleted on success).
//   - `deletion:<iso>:<agent_id>` — the deletion marker (the full envelope).
//     The marker is written in the same AGENTS namespace under a separate
//     key prefix; it is invisible to /list (which only iterates `agent:`).
//     Operators can later read the marker via the Cloudflare dashboard
//     or wrangler to cross-check against the local
//     directory/operator/.deletion-audit.log.

import { canonicalize } from "./canonicalize.js";

// v1: hardcoded single-operator allowlist (must stay in sync with
// submit.js). v2: read from env.ROOT_SYSTEM_POLICY (JSON) when set.
// Identical fallback policy to submit.js — the operator allowlist is
// the union of correctness of the directory's two write endpoints.
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
const AGENT_PREFIX = "agent:";
const DELETION_PREFIX = "deletion:";

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
  return jsonResponse(status, { ok: false, error: message, ...extra });
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

async function handleDelete(request, env) {
  const ROOT_SYSTEM_POLICY = loadRootSystemPolicy(env);
  if (!env[KV_BINDING]) {
    return bad(500, `${KV_BINDING} KV namespace not bound`);
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

  // Validate envelope shape. Note the kind guard: this is a
  // DIFFERENT envelope from the submit envelope. A submission body
  // POSTed here would be rejected as missing `kind`.
  const required = ["kind", "agent_id", "public_key", "submitted_at", "signature"];
  for (const k of required) {
    if (!(k in body)) return bad(400, `missing field: ${k}`);
  }
  if (body.kind !== "agent_deletion") {
    return bad(400, `unsupported kind: ${body.kind} (this endpoint only accepts agent_deletion)`);
  }
  if (typeof body.agent_id !== "string" || body.agent_id.length === 0) {
    return bad(400, "agent_id must be a non-empty string");
  }
  if (typeof body.public_key !== "string" || !body.public_key.startsWith("ed25519:")) {
    return bad(400, "public_key must be an ed25519: prefixed base64 string");
  }
  if (typeof body.submitted_at !== "string" || body.submitted_at.length === 0) {
    return bad(400, "submitted_at must be a non-empty ISO-8601 string");
  }
  if (typeof body.signature !== "string" || body.signature.length === 0) {
    return bad(400, "signature must be a non-empty base64 string");
  }

  // Read the existing entry (if any). We need it for two checks:
  //   1. To refuse 404 when the agent_id isn't in the catalog.
  //   2. To authorize self-delete: the envelope's `public_key` must
  //      equal the stored entry's `public_key` if the signer isn't
  //      an operator.
  const agentKey = `${AGENT_PREFIX}${body.agent_id}`;
  const existingRaw = await env[KV_BINDING].get(agentKey);
  if (!existingRaw) {
    return bad(404, "no such entry", { agent_id: body.agent_id });
  }
  let existing;
  try {
    existing = JSON.parse(existingRaw);
  } catch {
    // The KV value is unparseable — treat as a corrupt entry and refuse.
    return bad(500, "existing entry is corrupt", { agent_id: body.agent_id });
  }

  // Verify the signature against the envelope's claimed `public_key`.
  const { signature, ...signed } = body;
  const canonical = canonicalize(signed);
  const messageBytes = new TextEncoder().encode(canonical);

  let signatureOk = false;
  try {
    signatureOk = await verifySignature(body.public_key, messageBytes, signature);
  } catch (e) {
    signatureOk = false;
  }
  if (!signatureOk) {
    return bad(401, "signature did not verify", { agent_id: body.agent_id });
  }

  // Authorization: operator OR the entry's own key.
  let authorizedBy = null;
  for (const approver of ROOT_SYSTEM_POLICY.approvers) {
    if (approver.public_key === body.public_key) {
      authorizedBy = { name: approver.name, role: "operator" };
      break;
    }
  }
  if (!authorizedBy && existing.public_key === body.public_key) {
    authorizedBy = { name: `self:${body.agent_id}`, role: "self" };
  }
  if (!authorizedBy) {
    return bad(403, "public_key not authorized to delete this entry", {
      agent_id: body.agent_id,
      hint: "sign with one of the operators in ROOT_SYSTEM_POLICY, or with the entry's own public_key (self-delete)",
    });
  }

  // Write the deletion marker FIRST. If the marker write fails we
  // don't lose the entry; the operator can retry. The marker key
  // starts with `deletion:` so /list (which iterates `agent:` only)
  // never sees it.
  const markerKey = `${DELETION_PREFIX}${body.submitted_at}:${body.agent_id}`;
  const markerValue = JSON.stringify({
    envelope: body,
    authorized_by: authorizedBy,
    recorded_at: new Date().toISOString(),
  });
  try {
    await env[KV_BINDING].put(markerKey, markerValue);
  } catch (e) {
    return bad(500, "failed to write deletion marker; entry not deleted", { detail: String(e) });
  }

  // Remove the live entry.
  await env[KV_BINDING].delete(agentKey);

  return jsonResponse(200, {
    ok: true,
    agent_id: body.agent_id,
    authorized_by: authorizedBy.name,
    deletion_marker_key: markerKey,
  });
}

export async function onRequestPost(context) {
  return handleDelete(context.request, context.env);
}

export async function onRequestGet() {
  // The /delete endpoint is write-only. GETs fall through with a
  // clear 405 so a curious operator typing the URL in a browser
  // sees "method not allowed" rather than the HTML landing page.
  return jsonResponse(405, { ok: false, error: "method not allowed; POST a signed deletion envelope to this URL" });
}

export async function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "POST, OPTIONS",
      "access-control-allow-headers": "content-type",
    },
  });
}