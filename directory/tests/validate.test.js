// Tests for the URL validation logic in _validate.js
// These cover the v2 change: http:// is now allowed for private/loopback hosts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { validateAgentCardUrl, isPrivateOrLoopbackHost } from "../pages/functions/_validate.js";

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
  // public:
  assert.equal(isPrivateOrLoopbackHost("8.8.8.8"), false);
  assert.equal(isPrivateOrLoopbackHost("172.15.255.255"), false); // 172.15 is public
  assert.equal(isPrivateOrLoopbackHost("172.32.0.0"), false); // 172.32 is public
  assert.equal(isPrivateOrLoopbackHost("192.169.0.0"), false); // 192.169 is public
  assert.equal(isPrivateOrLoopbackHost("11.0.0.0"), false);
  assert.equal(isPrivateOrLoopbackHost("100.64.0.1"), false); // CGNAT, not RFC1918
});

test("isPrivateOrLoopbackHost: IPv6", () => {
  assert.equal(isPrivateOrLoopbackHost("::1"), true);
  assert.equal(isPrivateOrLoopbackHost("[::1]"), true);
  assert.equal(isPrivateOrLoopbackHost("fc00::1"), true);
  assert.equal(isPrivateOrLoopbackHost("fd00::1"), true); // ULA
  assert.equal(isPrivateOrLoopbackHost("fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"), true);
  // public IPv6:
  assert.equal(isPrivateOrLoopbackHost("2001:db8::1"), false);
  assert.equal(isPrivateOrLoopbackHost("2606:4700:4700::1111"), false); // Cloudflare DNS
});

test("isPrivateOrLoopbackHost: malformed", () => {
  assert.equal(isPrivateOrLoopbackHost(""), false);
  assert.equal(isPrivateOrLoopbackHost("not-an-ip"), false);
  assert.equal(isPrivateOrLoopbackHost("999.999.999.999"), false);
  assert.equal(isPrivateOrLoopbackHost("192.168.1"), false); // too few octets
});
