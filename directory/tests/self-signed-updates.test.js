// Tests for v2 self-signed updates: an agent whose public_key matches an
// existing entry can update that entry without operator co-signing.
//
// Threat model:
//   - First-time submission: must be operator-signed (v1 behavior).
//   - Update: must be signed by either the entry's existing public_key
//     (self-sign) or an operator.
//   - The `public_key` field is immutable — a self-signed update can't
//     change it (would be an identity-pivot attack).
//   - All other fields can be updated.

import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign as edSign } from "node:crypto";
import { canonicalize } from "../pages/functions/canonicalize.js";
import { onRequestPost } from "../pages/functions/submit.js";

function makeKeys() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const pubRaw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  return { privateKey, pubB64: Buffer.from(pubRaw).toString("base64") };
}

function signEnvelope(envelope, privateKey) {
  const { signature, ...signed } = envelope;
  return edSign(null, Buffer.from(canonicalize(signed), "utf8"), privateKey).toString("base64");
}

function makeMockEnv({ approvers = [], initialEntries = {} }) {
  return {
    ROOT_SYSTEM_POLICY: { version: 1, approvers },
    AGENTS: {
      _store: new Map(Object.entries(initialEntries)),
      async get(k) { return this._store.get(k) ?? null; },
      async put(k, v) { this._store.set(k, v); },
      async list({ prefix }) {
        const keys = [];
        for (const k of this._store.keys()) if (k.startsWith(prefix)) keys.push({ name: k });
        return { keys };
      },
    },
  };
}

function makeRequest(body) {
  return new Request("https://hermes-a2a.dpmob.com/submit", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.1" },
    body: JSON.stringify(body),
  });
}

const APPROVER = {
  name: "Marc Smith (operator)",
  public_key: "ed25519:qebTERZot2aZKIDT4VF7hub0llZFlJwpFGu7EvZ/YYk=",
};

test("v2: first-time submission still requires operator signature (v1 regression)", async () => {
  globalThis.fetch = globalThis.fetch || (() => Promise.resolve(new Response(null, { status: 200 })));
  const k = makeKeys();
  const env = makeMockEnv({ approvers: [APPROVER] });

  // Sign with a non-operator key (the agent's own key, not the operator's)
  const envelope = {
    agent_id: "fresh_001",
    name: "Fresh Agent",
    agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
    public_key: "ed25519:" + k.pubB64,
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T20:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, k.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  // First-time submission by a non-operator MUST be rejected
  assert.equal(resp.status, 403, `expected 403 (signature did not verify), got ${resp.status}`);
});

test("v2: operator can submit first entry for an agent", async () => {
  const k = makeKeys();
  const operatorKey = makeKeys(); // we'll use this as the operator
  const env = makeMockEnv({
    approvers: [{ name: "Test Op", public_key: "ed25519:" + operatorKey.pubB64 }],
  });

  const envelope = {
    agent_id: "agent_001",
    name: "First Agent",
    agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
    public_key: "ed25519:" + k.pubB64, // agent's own pubkey
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T20:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, operatorKey.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  if (resp.status !== 200) console.error("DEBUG op-first:", await resp.text());
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);
});

test("v2: agent can self-sign an update if public_key matches stored entry", async () => {
  const agentKey = makeKeys();
  const operatorKey = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Op", public_key: "ed25519:" + operatorKey.pubB64 }],
    initialEntries: {
      "agent:agent_002": JSON.stringify({
        agent_id: "agent_002",
        name: "Original Name",
        agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
        public_key: "ed25519:" + agentKey.pubB64,
        capabilities: ["a2a_call"],
        declared_at: "2026-10-03T20:00:00Z",
        approved_by: "Test Op",
      }),
    },
  });

  // Self-signed update: same agent_id, same public_key, but new name + capabilities
  const update = {
    agent_id: "agent_002",
    name: "Updated Name",
    agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
    public_key: "ed25519:" + agentKey.pubB64, // SAME
    capabilities: ["a2a_call", "memory_share", "browser"],
    declared_at: "2026-10-03T20:00:00Z",
  };
  update.signature = signEnvelope(update, agentKey.privateKey);

  const resp = await onRequestPost({ request: makeRequest(update), env });
  if (resp.status !== 200) console.error("DEBUG self-sign:", await resp.text());
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);

  // Verify the new name and capabilities are stored
  const stored = JSON.parse(await env.AGENTS.get("agent:agent_002"));
  assert.equal(stored.name, "Updated Name");
  assert.deepEqual(stored.capabilities, ["a2a_call", "memory_share", "browser"]);
});

test("v2: agent cannot self-sign a public_key change (identity pivot attack)", async () => {
  const originalKey = makeKeys();
  const newKey = makeKeys(); // attacker controls this
  const operatorKey = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Op", public_key: "ed25519:" + operatorKey.pubB64 }],
    initialEntries: {
      "agent:agent_003": JSON.stringify({
        agent_id: "agent_003",
        name: "Pivoted",
        agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
        public_key: "ed25519:" + originalKey.pubB64,
        capabilities: ["a2a_call"],
        declared_at: "2026-10-03T20:00:00Z",
        approved_by: "Test Op",
      }),
    },
  });

  // Attacker tries to update agent_003 with a different public_key,
  // signed with the original (stolen) key. The function should reject
  // because the public_key field doesn't match the stored one.
  const update = {
    agent_id: "agent_003",
    name: "Hijacked",
    agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
    public_key: "ed25519:" + newKey.pubB64, // CHANGED — but signed with original
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T20:00:00Z",
  };
  update.signature = signEnvelope(update, originalKey.privateKey);

  const resp = await onRequestPost({ request: makeRequest(update), env });
  if (resp.status !== 400 && resp.status !== 403) {
    console.error("DEBUG pivot attack: unexpected status", resp.status, await resp.text());
  }
  // The public_key on the envelope must match the stored public_key.
  // Since the signatures were verified first (and the original key IS
  // in the store), the verify may pass; the immutability check then
  // fails. Either 400 (rejected by immutability) or 403 (signature)
  // is acceptable, but 200 is NOT.
  assert.notEqual(resp.status, 200, "pivot attack must not succeed");
});

test("v2: operator can override an existing entry (escape hatch)", async () => {
  const agentKey = makeKeys();
  const operatorKey = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Op", public_key: "ed25519:" + operatorKey.pubB64 }],
    initialEntries: {
      "agent:agent_004": JSON.stringify({
        agent_id: "agent_004",
        name: "Original",
        agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
        public_key: "ed25519:" + agentKey.pubB64,
        capabilities: ["a2a_call"],
        declared_at: "2026-10-03T20:00:00Z",
        approved_by: "Test Op",
      }),
    },
  });

  // Operator signs an update with a new name; the public_key is unchanged
  const opUpdate = {
    agent_id: "agent_004",
    name: "Operator Renamed",
    agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
    public_key: "ed25519:" + agentKey.pubB64, // same as stored
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T20:00:00Z",
  };
  opUpdate.signature = signEnvelope(opUpdate, operatorKey.privateKey);

  const resp = await onRequestPost({ request: makeRequest(opUpdate), env });
  assert.equal(resp.status, 200);
  const stored = JSON.parse(await env.AGENTS.get("agent:agent_004"));
  assert.equal(stored.name, "Operator Renamed");
  assert.equal(stored.approved_by, "Test Op"); // last writer wins, but they were operators
});
