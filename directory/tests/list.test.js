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

// --- v0.5.2: ?tailnet= filter ---

const MULTI_TAILNET_FIXTURES = [
  {
    agent_id: "tail_a",
    name: "Tail A",
    agent_card_url: "http://minisforum-desktop.taila6e2e.ts.net:9900/card.json",
    transport: "tailscale-magicdns",
  },
  {
    agent_id: "tail_b",
    name: "Tail B",
    agent_card_url: "http://someone.tailnetXYZ.ts.net:9900/card.json",
    transport: "tailscale-magicdns",
  },
  {
    agent_id: "lan_x",
    name: "LAN X",
    agent_card_url: "http://192.168.1.5:9900/card.json",
    transport: "lan",
  },
  {
    agent_id: "pub_y",
    name: "Pub Y",
    agent_card_url: "https://example.com/card.json",
    transport: "https",
  },
];

test("/list?tailnet=… filters to entries on the named Tailscale tailnet", async () => {
  const env = makeMockEnv(MULTI_TAILNET_FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?tailnet=taila6e2e"),
    env,
  });
  const body = await resp.json();
  assert.equal(body.count, 1);
  assert.equal(body.agents[0].agent_id, "tail_a");
  // The response also echoes the active filters.
  assert.equal(body.filters.tailnet, "taila6e2e");
});

test("/list?tailnet= is case-insensitive", async () => {
  const env = makeMockEnv(MULTI_TAILNET_FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?tailnet=TAILNETxyz"),
    env,
  });
  const body = await resp.json();
  assert.equal(body.count, 1);
  assert.equal(body.agents[0].agent_id, "tail_b");
});

test("/list?tailnet=.ts.net is a substring match across all MagicDNS entries", async () => {
  // The dot prefix signals substring match. .ts.net is present in every
  // MagicDNS host, so this should return both tail_a and tail_b but not
  // the LAN or public entries.
  const env = makeMockEnv(MULTI_TAILNET_FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?tailnet=.ts.net"),
    env,
  });
  const body = await resp.json();
  assert.equal(body.count, 2);
  const ids = body.agents.map((a) => a.agent_id).sort();
  assert.deepEqual(ids, ["tail_a", "tail_b"]);
});

test("/list?tailnet=… excludes non-MagicDNS entries", async () => {
  // LAN entries don't have a ts.net host, so they're filtered out
  // even though 192.168.1.5 contains '5'.
  const env = makeMockEnv(MULTI_TAILNET_FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?tailnet=192"),
    env,
  });
  const body = await resp.json();
  assert.equal(body.count, 0);
});

// --- v0.5.2: ?reachable_via= alias for ?transport= ---

test("/list?reachable_via=… is an alias for ?transport= (exact same effect)", async () => {
  const env = makeMockEnv(FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?reachable_via=lan"),
    env,
  });
  const body = await resp.json();
  assert.equal(body.count, 1);
  assert.equal(body.agents[0].agent_id, "lan_1");
});

test("/list?transport= takes precedence over ?reachable_via= when both are set", async () => {
  // If both are set, transport wins. This avoids the discoverer being
  // confused by which param was applied.
  const env = makeMockEnv(FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?transport=lan&reachable_via=https"),
    env,
  });
  const body = await resp.json();
  assert.equal(body.count, 1);
  assert.equal(body.agents[0].agent_id, "lan_1");
  // body.filters echoes the active one.
  assert.equal(body.filters.transport, "lan");
});

// --- v0.5.2: combined filters ---

test("/list?tailnet=…&transport=… combines both filters", async () => {
  // Only tailnet=taila6e2e AND transport=tailscale-magicdns matches both
  // criteria. tail_a has both; tail_b has wrong tailnet.
  const env = makeMockEnv(MULTI_TAILNET_FIXTURES);
  const resp = await onRequestGet({
    request: makeRequest("https://hermes-a2a.dpmob.com/list?tailnet=taila6e2e&transport=tailscale-magicdns"),
    env,
  });
  const body = await resp.json();
  assert.equal(body.count, 1);
  assert.equal(body.agents[0].agent_id, "tail_a");
  assert.equal(body.filters.transport, "tailscale-magicdns");
  assert.equal(body.filters.tailnet, "taila6e2e");
});