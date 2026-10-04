// variant-list.js
// Shared agent-list fetcher for the three landing-page variants.
// Tries /list (KV-backed) first, falls back to /agents.json (static seed),
// and renders into a <div id="agent-list" data-mode="...">.
//
// Modes:
//   "cards"   — variant-a: 3-up card grid, each card shows name,
//               description (2-line clamp), capability chips, "view →" link.
//   "rows"    — variant-b: dense operator rows, monospace, public-key
//               fingerprint chip, capability pills, last-verified timestamp.
//   "list"    — variant-c: quiet editorial list, name + one line, rule separators.
//
// The hero's "N agents in the directory" stat picks up a <span
// data-agent-count> if present, so each variant can show or hide it
// without re-implementing the fetch.

(function () {
  "use strict";

  // Shorten an ed25519 public key to a 4-char-prefix/suffix fingerprint,
  // like qebT…YYk=. Pure presentation — the full key is in /list.
  function fingerprint(pk) {
    if (!pk || typeof pk !== "string") return "";
    if (pk.startsWith("ed25519:")) pk = pk.slice("ed25519:".length);
    if (pk.length <= 8) return pk;
    return pk.slice(0, 4) + "…" + pk.slice(-4);
  }

  // YYYY-MM-DD from an ISO timestamp. Falls back to the input if it
  // doesn't parse, so the page never shows "Invalid Date".
  function shortDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().slice(0, 10);
  }

  // Single capability chip. Variants style the .cap-chip class.
  function chip(cap) {
    const span = document.createElement("span");
    span.className = "cap-chip";
    span.textContent = cap;
    return span;
  }

  // --- Mode renderers ----------------------------------------------------

  function renderCards(agents) {
    const grid = document.createElement("div");
    grid.className = "agent-grid";
    for (const a of agents) {
      const card = document.createElement("a");
      card.className = "agent-card";
      card.href = "/agent/" + a.agent_id + ".html";

      const name = document.createElement("h3");
      name.className = "agent-card-name";
      name.textContent = a.name;
      card.appendChild(name);

      if (a.description) {
        const desc = document.createElement("p");
        desc.className = "agent-card-desc";
        desc.textContent = a.description;
        card.appendChild(desc);
      }

      const caps = document.createElement("div");
      caps.className = "agent-card-caps";
      (a.capabilities || []).slice(0, 4).forEach((c) => caps.appendChild(chip(c)));
      card.appendChild(caps);

      const foot = document.createElement("div");
      foot.className = "agent-card-foot";
      const verified = document.createElement("span");
      verified.className = "agent-card-verified";
      verified.textContent = "verified " + shortDate(a.last_verified);
      foot.appendChild(verified);
      const arrow = document.createElement("span");
      arrow.className = "agent-card-arrow";
      arrow.textContent = "view →";
      foot.appendChild(arrow);
      card.appendChild(foot);

      grid.appendChild(card);
    }
    return grid;
  }

  function renderRows(agents) {
    const wrap = document.createElement("div");
    wrap.className = "agent-rows";
    for (const a of agents) {
      const row = document.createElement("a");
      row.className = "agent-row";
      row.href = "/agent/" + a.agent_id + ".html";

      // left column: name + description + meta line
      const main = document.createElement("div");
      main.className = "agent-row-main";
      const name = document.createElement("div");
      name.className = "agent-row-name";
      name.textContent = a.name;
      main.appendChild(name);
      if (a.description) {
        const desc = document.createElement("div");
        desc.className = "agent-row-desc";
        desc.textContent = a.description;
        main.appendChild(desc);
      }
      const meta = document.createElement("div");
      meta.className = "agent-row-meta";
      const ver = document.createElement("span");
      ver.textContent = "verified " + shortDate(a.last_verified);
      meta.appendChild(ver);
      if (a.approved_by) {
        const dot = document.createElement("span");
        dot.className = "agent-row-dot";
        dot.textContent = "·";
        meta.appendChild(dot);
        const by = document.createElement("span");
        by.textContent = "approved by " + a.approved_by;
        meta.appendChild(by);
      }
      main.appendChild(meta);
      row.appendChild(main);

      // right column: key fingerprint + capabilities
      const side = document.createElement("div");
      side.className = "agent-row-side";
      const fp = document.createElement("code");
      fp.className = "agent-row-fp";
      fp.textContent = fingerprint(a.public_key);
      fp.title = a.public_key || "";
      side.appendChild(fp);
      const caps = document.createElement("div");
      caps.className = "agent-row-caps";
      (a.capabilities || []).slice(0, 5).forEach((c) => caps.appendChild(chip(c)));
      side.appendChild(caps);
      row.appendChild(side);

      wrap.appendChild(row);
    }
    return wrap;
  }

  function renderList(agents) {
    const ul = document.createElement("ul");
    ul.className = "agent-list";
    for (const a of agents) {
      const li = document.createElement("li");
      li.className = "agent-list-item";

      const link = document.createElement("a");
      link.className = "agent-list-link";
      link.href = "/agent/" + a.agent_id + ".html";

      const name = document.createElement("span");
      name.className = "agent-list-name";
      name.textContent = a.name;
      link.appendChild(name);

      const desc = document.createElement("span");
      desc.className = "agent-list-desc";
      desc.textContent = a.description || "";
      link.appendChild(desc);

      const meta = document.createElement("span");
      meta.className = "agent-list-meta";
      const caps = (a.capabilities || []).slice(0, 3).join(" · ");
      meta.textContent = caps || "—";
      link.appendChild(meta);

      li.appendChild(link);
      ul.appendChild(li);
    }
    return ul;
  }

  // --- Wiring ------------------------------------------------------------

  function render(agents) {
    const root = document.getElementById("agent-list");
    if (!root) return;
    const mode = root.getAttribute("data-mode") || "cards";

    // Stat: every variant has at least one [data-agent-count] target.
    const countTargets = document.querySelectorAll("[data-agent-count]");
    countTargets.forEach((el) => {
      el.textContent = agents.length;
      el.hidden = false;
    });

    if (agents.length === 0) {
      root.innerHTML = "<p class='agent-empty'>No agents listed yet — be the first.</p>";
      return;
    }

    let node;
    if (mode === "rows") node = renderRows(agents);
    else if (mode === "list") node = renderList(agents);
    else node = renderCards(agents);
    root.replaceChildren(node);
  }

  function showError(msg) {
    const root = document.getElementById("agent-list");
    if (!root) return;
    root.innerHTML = "<p class='agent-empty'>" + msg + "</p>";
    // Hide the stat so we don't show "0 agents" misleadingly.
    document.querySelectorAll("[data-agent-count]").forEach((el) => {
      el.hidden = true;
    });
  }

  fetch("/list")
    .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
    .then((data) => render(data.agents || []))
    .catch(() =>
      fetch("/agents.json")
        .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
        .then((data) => render(data.agents || []))
        .catch((e) => showError("Could not load catalog: " + e))
    );
})();
