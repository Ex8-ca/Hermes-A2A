// Pages Function: /list
//
// Returns the current agent catalog as JSON. Reads from the AGENTS
// KV namespace.

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

  // v0.4.1: optional ?transport= filter. Case-insensitive match against
  // entry.transport (which is set by submit.js from the inference in
  // classifyTransport()). Absent param = no filtering (return all).
  // Entries without a transport field (e.g. pre-v0.4.1 live entries
  // like desktop_2 and ai5080) are still returned on unfiltered calls;
  // they simply won't match a transport-filtered call until they're
  // re-submitted.
  let transportFilter = null;
  try {
    const url = new URL(request.url);
    const t = url.searchParams.get("transport");
    if (t && t.length > 0) {
      transportFilter = t.toLowerCase();
    }
  } catch {
    // request.url is opaque or invalid; treat as no filter.
  }
  const filtered = transportFilter == null
    ? agents
    : agents.filter((a) => typeof a.transport === "string"
        && a.transport.toLowerCase() === transportFilter);

  return jsonResponse(200, { version: 1, count: filtered.length, agents: filtered });
}
