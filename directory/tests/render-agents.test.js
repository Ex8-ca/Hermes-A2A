// Tests for the per-agent page renderer's behavior.
//
// The renderer bakes per-agent data (name, description, capabilities,
// agent_card_url) into the static HTML so the page works without JS
// and shows a complete card even when the live catalog fetch fails.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { render } from "../render.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEMPLATE_PATH = join(__dirname, "..", "pages", "agent", "_template.html");
const TEMPLATE = readFileSync(TEMPLATE_PATH, "utf8");
const templateWith = () => TEMPLATE;

const FIXTURE_ENTRY = {
  agent_id: "agent_xyz",
  name: "X. Y. Zee",
  agent_card_url: "https://example.com/x/card.json",
  public_key: "ed25519:test",
  capabilities: ["a2a_call", "memory_share"],
  description: "A test fixture agent.",
  declared_at: "2026-10-03T22:00:00Z",
  approved_by: "Test Op",
};

test("render bakes the agent_id into the page (regression)", () => {
  const out = render(TEMPLATE, FIXTURE_ENTRY);
  // The id should be a JSON-stringified literal, not a URL parser
  assert.match(out, /const id = "agent_xyz";/);
});

test("render bakes the agent's data into the page so it works without JS", () => {
  const out = render(TEMPLATE, FIXTURE_ENTRY);
  // Name — note the H1 also contains the transport-chip span (v0.4.1).
  // The fixture has no transport, so the chip stays hidden. Older
  // (pre-v0.4.1) entries read identically.
  assert.match(
    out,
    /<h1 id="name">X\. Y\. Zee <span id="transport-chip" class="transport-chip" hidden><\/span><\/h1>/,
    "agent name should be baked into the H1",
  );
  // Description
  assert.match(out, /<p id="description">A test fixture agent\.<\/p>/);
  // Capabilities
  assert.match(out, /<li>a2a_call<\/li>/);
  assert.match(out, /<li>memory_share<\/li>/);
  // The card URL should appear as an actual link, not a placeholder
  assert.match(
    out,
    /href="https:\/\/example\.com\/x\/card\.json"/,
    "agent_card_url should be a real link in the rendered HTML",
  );
  // The link should be the visible text of the code element
  assert.match(out, /<code id="card-url">https:\/\/example\.com\/x\/card\.json<\/code>/);
});

test("render bakes a populated transport chip when entry.transport is set", () => {
  // v0.4.1: when entry.transport is one of the four recognized values,
  // the chip span in the H1 becomes visible and carries the variant
  // class. When transport is missing or unrecognized, the chip stays
  // hidden (see the test above).
  const out = render(TEMPLATE, { ...FIXTURE_ENTRY, transport: "tailscale-magicdns" });
  assert.match(
    out,
    /<span id="transport-chip" class="transport-chip transport-tailscale-magicdns">tailscale-magicdns<\/span>/,
    "transport chip should be populated and visible when transport is set",
  );
  assert.doesNotMatch(
    out,
    /<span id="transport-chip" class="transport-chip" hidden>tailscale-magicdns/,
    "hidden chip should not contain transport text",
  );
});

test("render escapes user-supplied name/description to avoid HTML injection", () => {
  const evil = {
    ...FIXTURE_ENTRY,
    name: '<script>alert("xss")</script>',
    description: "evil <img onerror=alert(1) src=x>",
  };
  const out = render(TEMPLATE, evil);
  // The raw script tag must NOT appear in the HTML
  assert.doesNotMatch(out, /<script>alert\("xss"\)<\/script>/);
  // HTML entities should escape the angle brackets
  assert.match(out, /&lt;script&gt;/);
  assert.match(out, /&lt;img onerror=alert\(1\) src=x&gt;/);
});

test("render sets the document title to the agent's name", () => {
  const out = render(TEMPLATE, FIXTURE_ENTRY);
  assert.match(out, /<title>X\. Y\. Zee — Hermes-A2A directory<\/title>/);
});

test("render does not break the existing 'back to directory' link", () => {
  const out = render(TEMPLATE, FIXTURE_ENTRY);
  assert.match(out, /href="\/">.*back to the directory/s);
});

test("render hides the capabilities section when capabilities is empty", () => {
  const out = render(TEMPLATE, { ...FIXTURE_ENTRY, capabilities: [] });
  // caps-section should be hidden
  assert.match(out, /<section id="caps-section" hidden>/);
  // The list should be empty
  assert.match(out, /<ul id="capabilities">\s*<\/ul>/);
});

test("render hides the description section when description is empty", () => {
  const out = render(TEMPLATE, { ...FIXTURE_ENTRY, description: "" });
  // desc-section should remain hidden
  assert.match(out, /<section id="desc-section" hidden>/);
  // No non-empty description text rendered
  assert.doesNotMatch(out, /<p id="description">[^<]*[^<]+<\/p>/);
});

test("render bakes the agent_card_url into the meeting request command", () => {
  const out = render(TEMPLATE, FIXTURE_ENTRY);
  // The cmd-peer span should contain the real card URL
  assert.match(out, /<span id="cmd-peer">https:\/\/example\.com\/x\/card\.json<\/span>/);
});

test("render keeps the live /list fetch in the script (for last_verified updates)", () => {
  const out = render(TEMPLATE, FIXTURE_ENTRY);
  // The script's data source is /list, not /agents.json
  assert.match(out, /fetch\("\/list"\)/);
  assert.doesNotMatch(out, /fetch\("\/agents\.json"\)/);
});
