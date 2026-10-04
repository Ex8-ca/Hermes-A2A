// Pages Function: /list
//
// Returns the current agent catalog as JSON. Reads from the AGENTS
// KV namespace.
//
// Filters (orthogonal; multiple can be combined):
//   ?transport=<value>  case-insensitive match against entry.transport
//                       (one of: tailscale-magicdns, lan, https, http-public)
//   ?tailnet=<name>     case-insensitive match against the tailnet
//                       suffix in entry.agent_card_url's host. For a host
//                       "ai5080.taila6e2e.ts.net", the tailnet is
//                       "taila6e2e" (the second-to-last label of the
//                       .ts.net host). If the param starts with ".",
//                       substring match (e.g. "?tailnet=.ts.net" matches
//                       every MagicDNS entry across tailnets). Exact
//                       match otherwise.
//   ?reachable_via=<value>  alias for ?transport= from the discoverer's
//                       perspective. Same semantics.
//
// Absent params = no filtering. Entries without the relevant field
// (e.g. pre-v0.4.1 live entries like desktop_2 and ai5080) are still
// returned on unfiltered calls; they simply won't match a filtered
// call until they're re-submitted.
//
// Response shape: { version: 1, count: N, agents: [...], filters: {...} }

const KV_BINDING = "AGENTS";

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
    },
  });
}

/** Extract the tailnet name from an agent_card_url.
 *
 * For a host like "ai5080.taila6e2e.ts.net" returns "taila6e2e".
 * For a host like "myhost.example.com" returns null (not a MagicDNS
 * host).
 * For a host like "host.tailnetXYZ.ts.net" returns "tailnetXYZ".
 *
 * Returns null if the URL can't be parsed or isn't a ts.net host.
 */
function extractTailnetFromUrl(agent_card_url) {
  if (typeof agent_card_url !== "string" || agent_card_url.length === 0) {
    return null;
  }
  let host;
  try {
    host = new URL(agent_card_url).hostname.toLowerCase();
  } catch {
    return null;
  }
  // Must end in .ts.net for there to be a tailnet name.
  if (!host.endsWith(".ts.net")) {
    return null;
  }
  // Strip ".ts.net" then take the last label.
  const withoutSuffix = host.slice(0, -".ts.net".length);
  const labels = withoutSuffix.split(".");
  if (labels.length === 0) return null;
  return labels[labels.length - 1];
}

/** Returns true if `agent_card_url` matches the `pattern` query-string value.
 *
 *  Behavior:
 *  - pattern empty -> match anything (but agent_card_url must be present)
 *  - pattern starts with "." -> substring match against the URL's host
 *    (e.g. ".ts.net" matches every MagicDNS entry across tailnets;
 *    ".example.com" matches every entry on *.example.com).
 *  - otherwise -> exact, case-insensitive match against the extracted
 *    tailnet name (the second-to-last label of a *.ts.net host). For
 *    non-ts.net hosts this returns false (no tailnet to match against).
 */
function matchesTailnetParam(agent_card_url, pattern) {
  if (pattern == null || pattern.length === 0) return agent_card_url != null;
  if (agent_card_url == null) return false;
  let host;
  try {
    host = new URL(agent_card_url).hostname.toLowerCase();
  } catch {
    return false;
  }
  const p = pattern.toLowerCase();
  if (p.startsWith(".")) {
    return host.includes(p);
  }
  const tn = extractTailnetFromUrl(agent_card_url);
  if (tn == null) return false;
  return tn === p;
}

export async function onRequestGet(context) {
  const { request, env } = context;
  if (!env[KV_BINDING]) {
    return jsonResponse(500, { error: `${KV_BINDING} KV namespace not bound` });
  }
  const list = await env[KV_BINDING].list({ prefix: "agent:" });
  const agents = [];
  for (const key of list.keys) {
    const raw = await env[KV_BINDING].get(key.name);
    if (raw) {
      try {
        agents.push(JSON.parse(raw));
      } catch {}
    }
  }

  // Parse filter params (all orthogonal).
  let transportFilter = null;
  let tailnetFilter = null;
  try {
    const url = new URL(request.url);
    const t = url.searchParams.get("transport");
    if (t && t.length > 0) {
      transportFilter = t.toLowerCase();
    }
    const tn = url.searchParams.get("tailnet");
    if (tn && tn.length > 0) {
      tailnetFilter = tn.toLowerCase();
    }
    // reachable_via is an alias for transport from the discoverer's POV.
    const rv = url.searchParams.get("reachable_via");
    if (rv && rv.length > 0 && transportFilter == null) {
      transportFilter = rv.toLowerCase();
    }
  } catch {
    // request.url is opaque or invalid; treat as no filter.
  }

  let filtered = agents;
  if (transportFilter != null) {
    filtered = filtered.filter(
      (a) => typeof a.transport === "string"
        && a.transport.toLowerCase() === transportFilter,
    );
  }
  if (tailnetFilter != null) {
    filtered = filtered.filter(
      (a) => matchesTailnetParam(a.agent_card_url, tailnetFilter),
    );
  }

  return jsonResponse(200, {
    version: 1,
    count: filtered.length,
    agents: filtered,
    filters: {
      transport: transportFilter,
      tailnet: tailnetFilter,
    },
  });
}