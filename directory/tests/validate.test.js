// Tests for the URL validation logic in _validate.js
// These cover the v2 change: http:// is now allowed for private/loopback hosts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { validateAgentCardUrl, isPrivateOrLoopbackHost, classifyTransport } from "../pages/functions/_validate.js";

test("validateAgentCardUrl accepts https://", () => {
  assert.deepEqual(
    validateAgentCardUrl("https://hermes-a2a.dpmob.com/agents.json"),
    { ok: true }
  );
});

test("validateAgentCardUrl accepts http:// to a private IPv4 host (192.168.x)", () => {
  assert.deepEqual(
    validateAgentCardUrl("http://192.168.1.2:9900/.well-known/agent-card.json"),
    { ok: true }
  );
});

test("validateAgentCardUrl accepts http:// to 10.x", () => {
  assert.deepEqual(validateAgentCardUrl("http://10.0.0.1/"), { ok: true });
});

test("validateAgentCardUrl accepts http:// to 172.16.x through 172.31.x", () => {
  assert.deepEqual(validateAgentCardUrl("http://172.16.0.1/"), { ok: true });
  assert.deepEqual(validateAgentCardUrl("http://172.20.5.5/"), { ok: true });
  assert.deepEqual(validateAgentCardUrl("http://172.31.255.254/"), { ok: true });
});

test("validateAgentCardUrl accepts http:// to 127.x loopback", () => {
  assert.deepEqual(validateAgentCardUrl("http://127.0.0.1:8080/foo"), { ok: true });
});

test("validateAgentCardUrl accepts http:// to IPv6 ::1 loopback", () => {
  assert.deepEqual(validateAgentCardUrl("http://[::1]:8080/foo"), { ok: true });
});

test("validateAgentCardUrl accepts http:// to IPv6 ULA (fc00::/7)", () => {
  assert.deepEqual(validateAgentCardUrl("http://[fc00::1]/"), { ok: true });
  assert.deepEqual(validateAgentCardUrl("http://[fd12:3456:789a::1]/"), { ok: true });
});

test("validateAgentCardUrl rejects http:// to a public IP", () => {
  const r = validateAgentCardUrl("http://8.8.8.8/agents.json");
  assert.equal(r.ok, false);
  assert.match(r.reason, /loopback or RFC1918/);
});

test("validateAgentCardUrl rejects http:// to a public hostname", () => {
  const r = validateAgentCardUrl("http://example.com/agents.json");
  assert.equal(r.ok, false);
  assert.match(r.reason, /loopback or RFC1918/);
});

test("validateAgentCardUrl rejects non-http(s) schemes", () => {
  for (const url of [
    "ftp://example.com/",
    "file:///etc/passwd",
    "data:text/plain,hello",
    "javascript:alert(1)",
    "ws://example.com/",
  ]) {
    const r = validateAgentCardUrl(url);
    assert.equal(r.ok, false, `expected ${url} to be rejected`);
  }
});

test("validateAgentCardUrl rejects malformed URLs", () => {
  for (const url of ["", "not a url", "://nope", null, undefined, 42]) {
    const r = validateAgentCardUrl(url);
    assert.equal(r.ok, false, `expected ${JSON.stringify(url)} to be rejected`);
    assert.ok(r.reason, "rejection includes a reason");
  }
});

test("isPrivateOrLoopbackHost: IPv4 ranges", () => {
  assert.equal(isPrivateOrLoopbackHost("127.0.0.1"), true);
  assert.equal(isPrivateOrLoopbackHost("10.0.0.0"), true);
  assert.equal(isPrivateOrLoopbackHost("10.255.255.255"), true);
  assert.equal(isPrivateOrLoopbackHost("172.16.0.1"), true);
  assert.equal(isPrivateOrLoopbackHost("172.31.255.255"), true);
  assert.equal(isPrivateOrLoopbackHost("192.168.1.1"), true);
  assert.equal(isPrivateOrLoopbackHost("0.0.0.0"), true);
  // Tailscale 100.64.0.0/10
  assert.equal(isPrivateOrLoopbackHost("100.64.0.0"), true);
  assert.equal(isPrivateOrLoopbackHost("100.88.26.20"), true);
  assert.equal(isPrivateOrLoopbackHost("100.127.255.255"), true);

  assert.equal(isPrivateOrLoopbackHost("8.8.8.8"), false);
  assert.equal(isPrivateOrLoopbackHost("172.15.255.255"), false); // 172.15 is public
  assert.equal(isPrivateOrLoopbackHost("172.32.0.0"), false); // 172.32 is public
  assert.equal(isPrivateOrLoopbackHost("192.169.0.0"), false); // 192.169 is public
  assert.equal(isPrivateOrLoopbackHost("11.0.0.0"), false);
  assert.equal(isPrivateOrLoopbackHost("100.63.255.255"), false); // just below Tailscale range
  assert.equal(isPrivateOrLoopbackHost("100.128.0.0"), false); // just above
});

test("isPrivateOrLoopbackHost: Tailscale MagicDNS", () => {
  assert.equal(isPrivateOrLoopbackHost("minisforum-desktop.taila6e2e.ts.net"), true);
  assert.equal(isPrivateOrLoopbackHost("foo.ts.net"), true);
  assert.equal(isPrivateOrLoopbackHost("Foo.TS.NET"), true); // case-insensitive
  assert.equal(isPrivateOrLoopbackHost("ts.net"), true);
  assert.equal(isPrivateOrLoopbackHost("notts.net"), false); // suffix must match
  assert.equal(isPrivateOrLoopbackHost("foo.example.com"), false);
  assert.equal(isPrivateOrLoopbackHost("foo.tailscale.us"), true);
});

test("isPrivateOrLoopbackHost: IPv6", () => {
  assert.equal(isPrivateOrLoopbackHost("::1"), true);
  assert.equal(isPrivateOrLoopbackHost("[::1]"), true);
  assert.equal(isPrivateOrLoopbackHost("fc00::1"), true);
  assert.equal(isPrivateOrLoopbackHost("fd00::1"), true); // ULA
  assert.equal(isPrivateOrLoopbackHost("fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"), true);
  assert.equal(isPrivateOrLoopbackHost("fe80::1"), true); // link-local
  assert.equal(isPrivateOrLoopbackHost("febf::1"), true); // still fe80::/10
  assert.equal(isPrivateOrLoopbackHost("fec0::1"), false); // site-local (deprecated)

  assert.equal(isPrivateOrLoopbackHost("2001:db8::1"), false);
  assert.equal(isPrivateOrLoopbackHost("2606:4700:4700::1111"), false); // Cloudflare DNS
});

test("isPrivateOrLoopbackHost: malformed", () => {
  assert.equal(isPrivateOrLoopbackHost(""), false);
  assert.equal(isPrivateOrLoopbackHost("not-an-ip"), false);
  assert.equal(isPrivateOrLoopbackHost("999.999.999.999"), false);
  assert.equal(isPrivateOrLoopbackHost("192.168.1"), false); // too few octets
});

// v0.4.1: classifyTransport() inference rules. The function is pure
// over the URL string; no DNS lookups.

test("classifyTransport: tailscale-magicdns for *.ts.net", () => {
  assert.equal(
    classifyTransport("http://minisforum-desktop.taila6e2e.ts.net:9900/.well-known/agent-card.json"),
    "tailscale-magicdns",
  );
  assert.equal(
    classifyTransport("https://foo.ts.net/card.json"),
    "tailscale-magicdns",
  );
  // case-insensitive
  assert.equal(
    classifyTransport("https://Foo.TS.NET/card.json"),
    "tailscale-magicdns",
  );
});

test("classifyTransport: lan for Tailscale 100.64/10 IP (https or http)", () => {
  // A literal Tailscale IP is private but not a MagicDNS name — it
  // should fall into "lan", not "tailscale-magicdns".
  assert.equal(classifyTransport("http://100.88.26.20:9900/card.json"), "lan");
  assert.equal(classifyTransport("https://100.64.0.1/card.json"), "lan");
});

test("classifyTransport: lan for RFC1918 IPv4 (192.168.x and 10.x)", () => {
  assert.equal(classifyTransport("http://192.168.1.2:9900/card.json"), "lan");
  assert.equal(classifyTransport("http://10.0.0.1/card.json"), "lan");
  assert.equal(classifyTransport("https://192.168.0.1/card.json"), "lan");
});

test("classifyTransport: https for public HTTPS endpoints", () => {
  assert.equal(classifyTransport("https://hermes-a2a.dpmob.com/agents.json"), "https");
  assert.equal(classifyTransport("https://example.com/card.json"), "https");
});

test("classifyTransport: http-public for plain http to a public host", () => {
  // Public host + plain http. validateAgentCardUrl would reject this
  // at submit time, but classifyTransport still produces a label so
  // manually-created KV entries (pre-v0.4.1, or hand-edited) show up
  // honestly in the catalog.
  assert.equal(classifyTransport("http://8.8.8.8/agents.json"), "http-public");
  assert.equal(classifyTransport("http://example.com/agents.json"), "http-public");
});

test("classifyTransport: null for invalid or empty URLs", () => {
  assert.equal(classifyTransport(""), null);
  assert.equal(classifyTransport("not a url"), null);
  assert.equal(classifyTransport("://nope"), null);
  assert.equal(classifyTransport(null), null);
  assert.equal(classifyTransport(undefined), null);
  assert.equal(classifyTransport(42), null);
});
