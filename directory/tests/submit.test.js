// Tests for the submit.js Pages Function handler.
// These run the actual handler (not just pure modules) to prove the
// v2 changes (HTTP allowed for private hosts) work end-to-end.

import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, createPrivateKey, createPublicKey, sign as edSign } from "node:crypto";
import { canonicalize } from "../pages/functions/canonicalize.js";
import { onRequestPost } from "../pages/functions/submit.js";

function makeKeys() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  // The wire format used by submit.js and the operator allowlist is
  // "ed25519:" + base64(RAW 32-byte public key), not base64(SPKI).
  // submit.js prepends the SPKI prefix internally before crypto.subtle.importKey.
  // The publicKey returned by generateKeyPairSync is itself a KeyObject, so
  // we export its SPKI form and take the last 32 bytes (the raw key).
  const pubSpki = publicKey.export({ format: "der", type: "spki" });
  const pubRaw = pubSpki.subarray(-32);
  if (pubRaw.length !== 32) {
    throw new Error(`expected 32 raw bytes, got ${pubRaw.length}`);
  }
  return {
    privateKey,
    publicKey,
    pubB64: Buffer.from(pubRaw).toString("base64"),
  };
}

function signEnvelope(envelope, privateKey) {
  const { signature, ...signed } = envelope;
  const canon = canonicalize(signed);
  // Node's ed25519 sign: first arg is the digest (null = don't pre-hash; ed25519
  // is a single-pass signature, not a hash-then-sign).
  const sig = edSign(null, Buffer.from(canon, "utf8"), privateKey);
  return sig.toString("base64");
}

function makeMockEnv({ approvers }) {
  return {
    ROOT_SYSTEM_POLICY: { version: 1, approvers },
    AGENTS: {
      _store: new Map(),
      async get(k) { return this._store.get(k) ?? null; },
      async put(k, v) { this._store.set(k, v); },
      async list({ prefix }) {
        const keys = [];
        for (const k of this._store.keys()) if (k.startsWith(prefix)) keys.push({ name: k });
        return { keys };
      },
    },
    // Required for cache binding if Pages adds it
  };
}

function makeRequest(body) {
  return new Request("https://hermes-a2a.dpmob.com/submit", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.1" },
    body: JSON.stringify(body),
  });
}

function makeFetchStub(urlsThatReturn200) {
  // The submit.js function does a HEAD on body.agent_card_url to verify the
  // agent is reachable. Tests provide a list of URLs that should HEAD-200.
  return async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (urlsThatReturn200.includes(url)) {
      return new Response(null, { status: 200 });
    }
    return new Response("not found", { status: 404 });
  };
}

test("submit accepts https:// agent_card_url (regression: v1 still works)", async () => {
  globalThis.fetch = makeFetchStub(["https://hermes-a2a.dpmob.com/agents.json"]);
  const k = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + k.pubB64 }],
  });
  const envelope = {
    agent_id: "test_https",
    name: "Test HTTPS",
    agent_card_url: "https://hermes-a2a.dpmob.com/agents.json",
    public_key: "ed25519:" + k.pubB64,
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T19:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, k.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  if (resp.status !== 200) {
    console.error("DEBUG https test body:", await resp.text());
  }
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);
});

test("submit accepts http://192.168.x.x (v2: private hosts)", async () => {
  globalThis.fetch = makeFetchStub(["http://192.168.1.2:9900/.well-known/agent-card.json"]);
  const k = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + k.pubB64 }],
  });
  const envelope = {
    agent_id: "test_http_private",
    name: "Test HTTP private",
    agent_card_url: "http://192.168.1.2:9900/.well-known/agent-card.json",
    public_key: "ed25519:" + k.pubB64,
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T19:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, k.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  if (resp.status !== 200) {
    console.error("DEBUG http test body:", await resp.text());
  }
  assert.equal(resp.status, 200, `expected 200, got ${resp.status} — check submit.js response body`);
});

test("submit rejects http:// to a public IP with a clear reason", async () => {
  // The HEAD check is bypassed for the URL validation — we expect the URL
  // check to fire before the HEAD. We don't even need a fetch stub.
  globalThis.fetch = makeFetchStub([]);
  const k = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + k.pubB64 }],
  });
  const envelope = {
    agent_id: "test_http_public",
    name: "Test HTTP public",
    agent_card_url: "http://8.8.8.8/agents.json",
    public_key: "ed25519:" + k.pubB64,
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T19:00:00Z",
  };
  envelope.signature = signEnvelope(envelope, k.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 400);
  const body = await resp.json();
  assert.match(body.error, /loopback or RFC1918/);
});

// v0.4.1: transport field is auto-populated by submit.js. The operator
// does not need to set it; classifyTransport() infers it from the
// agent_card_url.

test("submit auto-infers transport from agent_card_url (tailscale-magicdns)", async () => {
  globalThis.fetch = makeFetchStub([
    "http://minisforum-desktop.taila6e2e.ts.net:9900/.well-known/agent-card.json",
  ]);
  const k = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + k.pubB64 }],
  });
  const envelope = {
    agent_id: "test_transport_auto",
    name: "Test Transport Auto",
    agent_card_url: "http://minisforum-desktop.taila6e2e.ts.net:9900/.well-known/agent-card.json",
    public_key: "ed25519:" + k.pubB64,
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T19:00:00Z",
    // Note: no `transport` field — submit.js must infer it.
  };
  envelope.signature = signEnvelope(envelope, k.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 200);
  const stored = JSON.parse(env.AGENTS._store.get("agent:test_transport_auto"));
  assert.equal(stored.transport, "tailscale-magicdns");
});

test("submit accepts an explicit transport override from the body", async () => {
  globalThis.fetch = makeFetchStub([
    "http://minisforum-desktop.taila6e2e.ts.net:9900/.well-known/agent-card.json",
  ]);
  const k = makeKeys();
  const env = makeMockEnv({
    approvers: [{ name: "Test Operator", public_key: "ed25519:" + k.pubB64 }],
  });
  // The envelope has agent_card_url pointing at a LAN IP, but the
  // operator sets `transport: "lan"` explicitly (e.g. an operator who
  // hand-wrote an envelope and wants to override inference).
  const envelope = {
    agent_id: "test_transport_explicit",
    name: "Test Transport Explicit",
    agent_card_url: "http://192.168.1.5:9900/card.json",
    public_key: "ed25519:" + k.pubB64,
    capabilities: ["a2a_call"],
    declared_at: "2026-10-03T19:00:00Z",
    transport: "lan",
  };
  envelope.signature = signEnvelope(envelope, k.privateKey);

  const resp = await onRequestPost({ request: makeRequest(envelope), env });
  assert.equal(resp.status, 200);
  const stored = JSON.parse(env.AGENTS._store.get("agent:test_transport_explicit"));
  assert.equal(stored.transport, "lan");
});
