// validate.js — pure validation logic for directory submissions.
// Extracted from submit.js so it can be unit tested in plain Node
// without the Cloudflare Pages runtime.

/**
 * Decide whether an agent_card_url is acceptable for the directory.
 *
 * v1 rule: must be https://.
 * v2 rule: https:// always OK. http:// allowed only for private/loopback
 *         addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8,
 *         IPv6 loopback, IPv6 ULA fc00::/7). Other schemes (file://, ftp://,
 *         data:, javascript:, etc.) are rejected.
 *
 * @param {string} url
 * @returns {{ok: true} | {ok: false, reason: string}}
 */
export function validateAgentCardUrl(url) {
  if (typeof url !== "string" || url.length === 0) {
    return { ok: false, reason: "agent_card_url must be a non-empty string" };
  }

  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, reason: "agent_card_url is not a valid URL" };
  }

  if (parsed.protocol === "https:") {
    return { ok: true };
  }

  if (parsed.protocol === "http:") {
    if (isPrivateOrLoopbackHost(parsed.hostname)) {
      return { ok: true };
    }
    return {
      ok: false,
      reason: "http://agent_card_url requires a loopback or RFC1918 host",
    };
  }

  return {
    ok: false,
    reason: `agent_card_url scheme ${parsed.protocol} not allowed (use https:// or http://private-host)`,
  };
}

/**
 * Classify the transport of an agent_card_url so a discoverer can
 * see at-a-glance whether an entry is reachable over a tailnet, a
 * LAN, or the public internet. Pure function over the URL; does
 * NOT do DNS lookups. Used by submit.js to auto-populate the
 * `transport` field, and exposed for unit tests.
 *
 * Rules (v0.4.1):
 *  - "tailscale-magicdns" — host ends in .ts.net (operator on a Tailscale tailnet)
 *  - "lan"               — host is RFC1918 / loopback / link-local / ULA
 *                          / Tailscale 100.64/10 (operator on a private network)
 *  - "https"             — scheme is https:// to a non-private host
 *  - "http-public"       — scheme is http:// to a non-private host (rare;
 *                          validateAgentCardUrl already refuses this at
 *                          submit time, so seeing it here means a manually
 *                          created one in the KV namespace)
 *  - null                — URL is unparseable, has no host, or no scheme
 *
 * @param {string} url
 * @returns {"tailscale-magicdns" | "lan" | "https" | "http-public" | null}
 */
export function classifyTransport(url) {
  if (typeof url !== "string" || url.length === 0) return null;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const host = parsed.hostname;
  const proto = parsed.protocol;
  if (!host || !proto) return null;

  // *.ts.net is checked before the generic private-range check so it
  // gets its own label. isPrivateOrLoopbackHost() would also catch it
  // and lump it under "lan"; we want the more specific tag.
  const lower = host.toLowerCase();
  if (lower.endsWith(".ts.net") || lower === "ts.net") {
    return "tailscale-magicdns";
  }

  if (isPrivateOrLoopbackHost(host)) {
    return "lan";
  }

  if (proto === "https:") return "https";
  if (proto === "http:") return "http-public";

  // Other schemes (ftp, file, ws, etc.) are not a recognized transport.
  return null;
}

/**
 * True if the host is a loopback address or in an RFC1918 / RFC4193 private
 * range. IPv6 loopback (::1) and ULA (fc00::/7) are included; link-local
 * (fe80::/10) is included as a private network. Also matches the
 * Tailscale 100.64.0.0/10 CGNAT range and *.ts.net MagicDNS hostnames.
 * Public hostnames return false.
 *
 * "Unreachable from Cloudflare's edge" is the effective semantic: any
 * URL the directory's Cloudflare Pages Functions cannot probe should
 * be flagged here so the submitter skips the liveness check and the
 * validator allows the URL through.
 *
 * Hostname forms handled: literal IPv4 ("192.168.1.2"), literal IPv6
 * ("[::1]", "[fc00::1]"), and DNS names (resolved as a string check;
 * we do NOT do DNS lookups here, so DNS names that happen to be
 * private are not auto-allowed unless the suffix matches a known
 * private zone like .ts.net).
 *
 * @param {string} host
 * @returns {boolean}
 */
export function isPrivateOrLoopbackHost(host) {
  if (typeof host !== "string" || host.length === 0) return false;

  // IPv6: bracketed in URL.hostname? No — URL strips brackets from IPv6.
  // hostnames look like "::1", "fc00::1", or "[::1]" defensively.
  const h = host.startsWith("[") && host.endsWith("]")
    ? host.slice(1, -1)
    : host;

  // Tailscale MagicDNS: any *.ts.net (or *.beta.tailscale.net etc.)
  // is a private, non-internet-routable name. Cloudflare's edge
  // can't reach these. Match on the suffix before doing any IP
  // parsing so DNS hostnames are caught.
  const lower = h.toLowerCase();
  if (lower.endsWith(".ts.net") || lower === "ts.net") return true;
  if (lower.endsWith(".tailscale.us") || lower === "tailscale.us") return true;

  // IPv6 loopback
  if (h === "::1") return true;

  // IPv6 ULA: fc00::/7 (fc00-fdff) and link-local fe80::/10
  if (h.includes(":")) {
    const first = h.split(":")[0].toLowerCase().padStart(4, "0");
    const head = parseInt(first.slice(0, 2), 16);
    if ((head & 0xfe) === 0xfc) return true; // fc00-fdff (ULA)
    if (head === 0xfe && (parseInt(first.slice(2, 4), 16) & 0xc0) === 0x80) {
      // fe80::/10 (link-local)
      return true;
    }
    return false;
  }

  // IPv4
  const parts = h.split(".");
  if (parts.length !== 4) return false;
  const octets = parts.map((p) => {
    if (!/^\d{1,3}$/.test(p)) return NaN;
    const n = parseInt(p, 10);
    if (n < 0 || n > 255) return NaN;
    return n;
  });
  if (octets.some((n) => Number.isNaN(n))) return false;
  const [a, b] = octets;

  // 127.0.0.0/8 — loopback
  if (a === 127) return true;
  // 10.0.0.0/8
  if (a === 10) return true;
  // 172.16.0.0/12
  if (a === 172 && b >= 16 && b <= 31) return true;
  // 192.168.0.0/16
  if (a === 192 && b === 168) return true;
  // 100.64.0.0/10 — Tailscale (and CGNAT in general; the operator
  // tailnet is in this range)
  if (a === 100 && b >= 64 && b <= 127) return true;
  // 0.0.0.0 (unspecified)
  if (a === 0 && octets.every((n) => n === 0)) return true;

  return false;
}
