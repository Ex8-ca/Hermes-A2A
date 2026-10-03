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
  const { env } = context;
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
  return jsonResponse(200, { version: 1, count: agents.length, agents });
}
