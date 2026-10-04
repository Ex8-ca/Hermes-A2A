// render.js — pure rendering logic for per-agent profile pages.
// Extracted from render_agents.py so it can be unit tested in plain
// Node (the test file at directory/tests/render-agents.test.js uses this).
//
// Produces an HTML file per agent by baking the agent's id AND
// its full profile data into the template. The JS still runs to fetch
// the live catalog for `last_verified` updates, but the page is
// useful without JS (a static, complete agent card).

import { readFileSync } from "node:fs";

/**
 * HTML-escape a string for safe interpolation into HTML text content.
 * Not for attribute values (use escapeAttr for those), and not for
 * untrusted URL components.
 */
function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * HTML-escape a value for use inside an attribute. The same rules as
 * escapeHtml but called out for the attribute case so callers can
 * be explicit.
 */
function escapeAttr(s) {
  return escapeHtml(s);
}

/**
 * Render one agent's profile page. Takes the template string and the
 * entry dict from the /list catalog. Bakes the agent id, name,
 * description, capabilities, agent_card_url, declared_at, and
 * approved_by into the HTML so the page is complete without JS.
 *
 * The script block at the bottom still runs to fetch the live catalog
 * for any updates, but it can no-op on a static page that already
 * shows the right values.
 */
export function render(template, entry) {
  const agentId = entry.agent_id;
  const safeId = escapeJs(agentId);

  // 1) Replace the URL-parsing JS with a literal id constant.
  //    This was the v1 behavior and stays — fast and correct.
  const oldIdBlock = (
    "    const id = decodeURIComponent(\n"
    + '      location.pathname.replace(/^\\/agent\\//, "").replace(/\\.html$/, "")\n'
    + "    );\n"
    + '    document.getElementById("agent-id").textContent = id;'
  );
  const newIdBlock = (
    "    // Agent id is baked in by the build (see render.js).\n"
    + `    const id = ${JSON.stringify(agentId)};\n`
    + '    document.getElementById("agent-id").textContent = id;'
  );
  if (!template.includes(oldIdBlock)) {
    throw new Error("template marker for agent_id not found — template changed?");
  }
  let out = template.replace(oldIdBlock, newIdBlock, 1);

  // 2) Switch the data source from the static seed to /list, so the
  //    rendered page reflects whatever the catalog currently shows.
  out = out.replace('fetch("/agents.json")', 'fetch("/list")', 1);

  // 3) Bake the agent's data into the HTML for SSR (works without JS).
  //    The script's later DOM mutations are guarded by a "data-baked"
  //    flag so they don't overwrite our SSR'd content unnecessarily.
  const name = entry.name || agentId;
  const cardUrl = entry.agent_card_url || "";
  const description = entry.description || "";
  const capabilities = Array.isArray(entry.capabilities) ? entry.capabilities : [];
  const declaredAt = entry.declared_at || "";
  const approvedBy = entry.approved_by || "(self-signed)";

  // Title
  out = out.replace(
    "<title>Agent — Hermes-A2A directory</title>",
    `<title>${escapeHtml(name)} — Hermes-A2A directory</title>`,
  );

  // v0.4.1: transport chip SSR. The template ships with the chip
  // <span hidden>; we unhide + populate it when entry.transport is
  // one of the four recognized values. Older entries (pre-v0.4.1
  // live catalog) have no transport field — the chip stays hidden
  // and reads as if it weren't there at all.
  const TRANSPORT_VALUES = ["tailscale-magicdns", "lan", "https", "http-public"];
  const transport = typeof entry.transport === "string"
    && TRANSPORT_VALUES.includes(entry.transport)
    ? entry.transport
    : null;

  // H1 name. The template's H1 contains both the name placeholder
  // ("Loading…") and the transport-chip span (hidden by default,
  // unhide-and-populate below). We replace the whole block in one
  // go to keep the chip's position correct.
  const chipSsr = transport
    ? `<span id="transport-chip" class="transport-chip transport-${escapeHtml(transport)}">${escapeHtml(transport)}</span>`
    : `<span id="transport-chip" class="transport-chip" hidden></span>`;
  out = out.replace(
    '<h1 id="name">Loading… <span id="transport-chip" class="transport-chip" hidden></span></h1>',
    `<h1 id="name">${escapeHtml(name)} ${chipSsr}</h1>`,
    1,
  );

  // Card link target — the SSR'd href is the real card URL so right-click
  // "open in new tab" works even with JS disabled.
  out = out.replace(
    '<a id="card-link" href="#"><code id="card-url"></code></a>',
    `<a id="card-link" href="${escapeAttr(cardUrl)}" target="_blank" rel="noopener"><code id="card-url">${escapeHtml(cardUrl)}</code></a>`,
  );

  // Capabilities list (SSR)
  const capsHtml = capabilities.length > 0
    ? capabilities.map((c) => `      <li>${escapeHtml(c)}</li>`).join("\n")
    : "";
  out = out.replace(
    '  <ul id="capabilities"></ul>',
    `  <ul id="capabilities">\n${capsHtml}\n  </ul>`,
  );
  if (capabilities.length === 0) {
    out = out.replace(
      '  <section id="caps-section">',
      '  <section id="caps-section" hidden>',
    );
  }

  // Description (SSR; show the section)
  if (description) {
    out = out.replace(
      '      <p id="description"></p>',
      `      <p id="description">${escapeHtml(description)}</p>`,
    );
    out = out.replace(
      '    <section id="desc-section" hidden>',
      '    <section id="desc-section">',
    );
  }

  // Provenance: declared_at + approved_by (last_verified only the live
  // catalog knows, leave that to the script)
  out = out.replace(
    '        <li id="declared"></li>',
    `        <li id="declared"><span>Declared at: ${escapeHtml(declaredAt)}</span></li>`,
  );
  out = out.replace(
    '        <li id="signature"></li>',
    `        <li id="signature"><span>Operator signature: ${escapeHtml(approvedBy)}</span></li>`,
  );

  // 4) Insert the agent's card URL as the cmd-peer text (the meeting
  //    request command example uses it). The script was setting this
  //    dynamically; we set it at build time now.
  out = out.replace(
    '<span id="cmd-peer"></span>',
    `<span id="cmd-peer">${escapeHtml(cardUrl)}</span>`,
  );

  return out;
}

function escapeJs(s) {
  return JSON.stringify(String(s));
}

/**
 * Load a template from disk. Convenience for the CLI entry point.
 */
export function loadTemplate(path) {
  return readFileSync(path, "utf8");
}
