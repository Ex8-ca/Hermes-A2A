// Tests for delete.js — the operator-signed (or self-signed) deletion
// endpoint that removes an entry from the AGENTS KV namespace.
//
// Run with: node --test directory/tests/delete.test.js
//
// Threat model:
//   - Operator allowlist (ROOT_SYSTEM_POLICY) can delete any entry.
//   - An agent's own stored public_key can delete ONLY that entry
//     (self-delete — defense in depth).
//   - Any other key: 403.
//   - Tampered signature: 401.
//   - Missing entry: 404.
//   - GET on /delete: 405 (write-only endpoint).

import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign as edSign } from "node:crypto";
import { canonicalize } from "../pages/functions/canonicalize.js";
import { onRequestPost, onRequestGet } from "../pages/functions/delete.js";

function makeKeys() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const pubRaw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  if (pubRaw.length !== 32) throw new Error(`expected 32 raw bytes, got ${pubRaw.length}`);
  return {
    privateKey,
    pubB64: Buffer.from(pubRaw).toString("base64"),
  };
}

function signEnvelope(envelope, privateKey) {
  const { signature, ...signed } = envelope;
  const canon = canonicalize(signed);
  return edSign(null, Buffer.from(canon, "utf8"), privateKey).toString("base64");
}

function makeMockEnv({ approvers = [], initialEntries = {} }) {
  return {
    ROOT_SYSTEM_POLICY: { version: 1, approvers },
    AGENTS: {
      _store: new Map(Object.entries(initialEntries)),
      async get(k) { return this._store.get(k) ?? null; },
      async put(k, v) { this._store.set(k, v); },
      async delete(k) { this._store.delete(k); },
      async list({ prefix }) {
        const keys = [];
        for (const k of this._store.keys()) if (k.startsWith(prefix)) keys.push({ name: k });
        return { keys };
      },
    },
  };
}

function makeRequest(body) {
  return new Request("https://hermes-a2a.dpmob.com/delete", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.1" },
    body: JSON.stringify(body),
  });
}

const ENTRY_DESKTOP_2 = {
  agent_id: "desktop_2",
  name: ".2 (desktop)",
  agent_card_url: "http://192.168.1.2:9900/.well-known/agent-card.json",
  public_key: "ed25519:OPERATOR_KEY_PLACEHOLDER", // overridden per-test
  capabilities: ["a2a_call", "memory", "skills", "terminal", "browser"],
  description: "Hermes Agent on .2.",
  declared_at: "2026-10-03T18:55:00Z",
  last_verified: "2026-10-04T00:00:00Z",
  approved_by: "Marc Smith (operator)",
};

test("operator can delete an entry", async () => {
  const op = makeKeys();
  const initial = {
    "agent:desktop_2": JSON.stringify({ ...ENTRY_DESKTOP_2, public_key: "ed25519:" + op.pubB64 }),
  };
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + op.pubB64 }],
    initialEntries: initial,
  });
  const envelope = {
    kind: "agent_deletion",
    agent_id: "desktop_2",
    public_key: "ed25519:" + op.pubB64,
    submitted_at: "2026-10-04T01:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, op.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);
  assert.equal(body.agent_id, "desktop_2");
  // Entry is removed.
  assert.equal(env.AGENTS._store.get("agent:desktop_2"), undefined);
  // Deletion marker exists under the deletion: prefix.
  const markerKeys = [...env.AGENTS._store.keys()].filter((k) => k.startsWith("deletion:"));
  assert.equal(markerKeys.length, 1);
  const marker = JSON.parse(env.AGENTS._store.get(markerKeys[0]));
  assert.equal(marker.envelope.agent_id, "desktop_2");
  assert.equal(marker.authorized_by.role, "operator");
});

test("self-delete: the entry's own public_key can delete that entry", async () => {
  const op = makeKeys();
  const agent = makeKeys();
  const initial = {
    "agent:self_sign_entry": JSON.stringify({
      agent_id: "self_sign_entry",
      name: "Self-Sign Entry",
      agent_card_url: "http://192.168.1.99:9900/.well-known/agent-card.json",
      public_key: "ed25519:" + agent.pubB64,
      capabilities: ["a2a_call"],
      declared_at: "2026-10-03T20:00:00Z",
      last_verified: "2026-10-03T20:00:00Z",
      approved_by: "self:self_sign_entry",
    }),
  };
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + op.pubB64 }],
    initialEntries: initial,
  });
  const envelope = {
    kind: "agent_deletion",
    agent_id: "self_sign_entry",
    public_key: "ed25519:" + agent.pubB64,
    submitted_at: "2026-10-04T01:30:00Z",
  };
  envelope.signature = signEnvelope(envelope, agent.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);
  assert.equal(body.authorized_by, "self:self_sign_entry");
  assert.equal(env.AGENTS._store.get("agent:self_sign_entry"), undefined);
});

test("invalid signature returns 401", async () => {
  const op = makeKeys();
  const initial = {
    "agent:desktop_2": JSON.stringify({ ...ENTRY_DESKTOP_2, public_key: "ed25519:" + op.pubB64 }),
  };
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + op.pubB64 }],
    initialEntries: initial,
  });
  const envelope = {
    kind: "agent_deletion",
    agent_id: "desktop_2",
    public_key: "ed25519:" + op.pubB64,
    submitted_at: "2026-10-04T01:00:00Z",
    signature: "AAAA" + "B".repeat(80), // 64 bytes of wrong signature (base64-encoded)
  };
  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 401);
  const body = await resp.json();
  assert.match(body.error, /signature did not verify/);
  // Entry is NOT removed.
  assert.ok(env.AGENTS._store.get("agent:desktop_2"));
});

test("unauthorized public_key returns 403", async () => {
  const op = makeKeys();
  const bystander = makeKeys();
  const initial = {
    "agent:desktop_2": JSON.stringify({ ...ENTRY_DESKTOP_2, public_key: "ed25519:" + op.pubB64 }),
  };
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + op.pubB64 }],
    initialEntries: initial,
  });
  const envelope = {
    kind: "agent_deletion",
    agent_id: "desktop_2",
    public_key: "ed25519:" + bystander.pubB64,
    submitted_at: "2026-10-04T01:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, bystander.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 403);
  const body = await resp.json();
  assert.match(body.error, /not authorized/);
  // Entry is NOT removed.
  assert.ok(env.AGENTS._store.get("agent:desktop_2"));
});

test("non-existent entry returns 404", async () => {
  const op = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + op.pubB64 }],
    initialEntries: {},
  });
  const envelope = {
    kind: "agent_deletion",
    agent_id: "agent_does_not_exist",
    public_key: "ed25519:" + op.pubB64,
    submitted_at: "2026-10-04T01:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, op.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 404);
  const body = await resp.json();
  assert.equal(body.error, "no such entry");
});

test("submit envelope (wrong kind) is rejected with 400", async () => {
  // A submission body POSTed to /delete must be rejected. The kind
  // guard prevents accidental cross-endpoint reuse.
  const op = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + op.pubB64 }],
    initialEntries: {},
  });
  const envelope = {
    agent_id: "agent_x",
    name: "X",
    agent_card_url: "https://example.com/.well-known/agent-card.json",
    public_key: "ed25519:" + op.pubB64,
    capabilities: ["a2a_call"],
    declared_at: "2026-10-04T01:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, op.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 400);
  const body = await resp.json();
  assert.match(body.error, /missing field: kind|unsupported kind/);
});

test("GET on /delete returns 405", async () => {
  const env = makeMockEnv({ approvers: [], initialEntries: {} });
  const resp = await onRequestGet({ env });
  assert.equal(resp.status, 405);
  const body = await resp.json();
  assert.equal(body.ok, false);
  assert.match(body.error, /method not allowed/);
});