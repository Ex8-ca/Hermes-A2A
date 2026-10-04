// Pages Function: /agent/<id>
//
// Cloudflare Pages auto-strips .html from static asset paths
// (so /agent/foo.html → 308 → /agent/foo). That is fine for browsers
// but breaks curl-style verification that expects a 200 on the
// .html URL. This function intercepts /agent/<id> (with or without
// a .html suffix) and serves the underlying static file (the
// no-suffix path) directly with a 200. /agent/foo (no extension)
// already returns 200 via the static handler, so we pass those
// through; the .html variant is the one this function actually
// rewrites.
//
// Routes:
//   /agent/agent_<id>.html  →  200 with the static body
//   /agent/agent_<id>       →  passed through to static handler
//
// File naming: Pages Functions dynamic segments use square brackets
// in the filename. `[id].js` matches a single path segment after
// /agent/. The captured segment may include a trailing .html.

export async function onRequestGet(context) {
  const { request, params } = context;
  const id = params.id || "";
  if (!id.endsWith(".html")) {
    // Not the .html variant — let the static handler serve the bare
    // path. /agent/<id> already returns 200 directly.
    return context.next();
  }
  const assetPath = "/agent/" + id.slice(0, -5);
  const url = new URL(request.url);
  url.pathname = assetPath;
  const assetResp = await fetch(url.toString(), {
    method: request.method,
    headers: request.headers,
    redirect: "follow",
  });
  if (!assetResp.ok) {
    return context.next();
  }
  const headers = new Headers(assetResp.headers);
  headers.set("x-served-by", "agent-html-redirect-fn");
  return new Response(assetResp.body, {
    status: 200,
    statusText: assetResp.statusText || "OK",
    headers,
  });
}