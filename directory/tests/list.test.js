// Tests for the /list Pages Function handler.
// v0.4.1: ?transport= filter (case-insensitive). The handler is pure
// over the KV namespace contents; we mock the binding in-process.

import { test } from "node:test";
import assert from "node:assert/strict";
import { onRequestGet } from "../pages/functions/list.js";

function makeMockEnv(entries) {
  // entries: an array of plain objects. They are stored verbatim at
  // `agent:<agent_id>` so /list.js's `KV.list({prefix:"agent:"})` and
  // `KV.get` both work without any extra plumbing.
  const store = new Map();
  for (const e of entries) {
    store.set(`agent:${e.agent_id}`, JSON.stringify(e));
  }
  return {
    AGENTS: {
      _store: store,
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

function makeRequest(url) {
  // /list reads request.url via new URL(...). A plain GET with the URL
  // we want is enough.
  return new Request(url, { method: "GET" });
}

const FIXTURES = [
  {
    agent_id: "tail_1",
    name: "Tail Agent",
    agent_card_url: "http://minisforum-desktop.taila6e2e.ts.net:9900/card.json",
    transport: "tailscale-magicdns",
  },
  {
    agent_id: "lan_1",
    name: "LAN Agent",
    agent_card_url: "http://192.168.1.2:9900/card.json",
    transport: "lan",
  },
  {
    agent_id: "pub_1",
    name: "Public HTTPS",
    agent_card_url: "https://example.com/card.json",
    transport: "https",
  },
  // v0.3.x legacy entry — no transport field. /list still returns it
  // on an unfiltered call; it just won't match a transport filter.
  {
    agent_id: "legacy_1",
    name: "Legacy Entry",
    agent_card_url: "https://old.example.com/card.json",
    // no transport field
  },
];

test("/list returns all entries when no transport filter is set", async () => {
  const env = makeMockEnv(FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list"),
    env,
  });
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.count, 4);
  assert.equal(body.agents.length, 4);
});

test("/list?transport=… filters to entries with matching transport", async () => {
  const env = makeMockEnv(FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?transport=lan"),
    env,
  });
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.count, 1);
  assert.equal(body.agents.length, 1);
  assert.equal(body.agents[0].agent_id, "lan_1");
});

test("/list?transport=… is case-insensitive", async () => {
  // UPPER and mixed-case filter values should match the lowercase-stored
  // entry.transport field.
  const env = makeMockEnv(FIXTURES);

  const upper = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?transport=TAILSCALE-MAGICDNS"),
    env,
  });
  const upperBody = await upper.json();
  assert.equal(upperBody.count, 1);
  assert.equal(upperBody.agents[0].agent_id, "tail_1");

  const mixed = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?transport=Https"),
    env,
  });
  const mixedBody = await mixed.json();
  assert.equal(mixedBody.count, 1);
  assert.equal(mixedBody.agents[0].agent_id, "pub_1");
});