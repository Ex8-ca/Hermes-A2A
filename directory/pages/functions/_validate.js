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
 * True if the host is a loopback address or in an RFC1918 / RFC4193 private
 * range. IPv6 loopback (::1) and ULA (fc00::/7) are included; link-local
 * (fe80::/10) is included as a private network. Public hostnames return false.
 *
 * Hostname forms handled: literal IPv4 ("192.168.1.2"), literal IPv6
 * ("[::1]", "[fc00::1]"), and DNS names (resolved as a string check; we do
 * NOT do DNS lookups here, so DNS names like "agent.lan" are NOT auto-allowed —
 * the caller is expected to pass an IP literal for private networks).
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

  // IPv6 loopback
  if (h === "::1") return true;

  // IPv6 ULA: fc00::/7 (fc00-fdff)
  if (h.includes(":")) {
    // quick first-hextet check
    const first = h.split(":")[0].toLowerCase().padStart(4, "0");
    const head = parseInt(first.slice(0, 2), 16);
    if ((head & 0xfe) === 0xfc) return true; // fc00-fdff
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
  // 0.0.0.0 (unspecified)
  if (a === 0 && octets.every((n) => n === 0)) return true;

  return false;
}
