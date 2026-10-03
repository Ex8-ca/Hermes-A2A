#!/usr/bin/env node
// Test the directory Worker's verifySignature + canonicalize against a
// Python-signed envelope. Run after tools/make_test_envelope.py.
//
//   .venv-test/bin/python directory/worker/tools/make_test_envelope.py
//   node directory/worker/tools/test_worker_verify.mjs
//
// Should print "OK" on success. Anything else is a bug in the Worker's
// verifier.

import { readFileSync, existsSync } from "node:fs";

const PUB_B64_PATH = "/tmp/dir_test_pub_b64.txt";
const ENVELOPE_PATH = "/tmp/dir_test_envelope.json";

if (!existsSync(PUB_B64_PATH) || !existsSync(ENVELOPE_PATH)) {
  console.error("Missing test files. Run tools/make_test_envelope.py first.");
  process.exit(1);
}

const pubB64Raw = readFileSync(PUB_B64_PATH, "utf-8").trim();
const envelope = JSON.parse(readFileSync(ENVELOPE_PATH, "utf-8"));

// ── Mirror the Worker's canonicalize ─────────────────────────────────
function canonicalize(obj) {
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) {
    return "[" + obj.map(canonicalize).join(",") + "]";
  }
  const keys = Object.keys(obj).sort();
  return (
    "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalize(obj[k])).join(",") + "}"
  );
}

const { signature, ...signed } = envelope;
const canon = canonicalize(signed);
console.log("canonical:", canon);

// ── Mirror the Worker's b64ToBytes ───────────────────────────────────
function b64ToBytes(b64) {
  const cleaned = b64.replace(/^ed25519:/, "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = cleaned + "===".slice((cleaned.length + 3) % 4);
  const bin = Buffer.from(padded, "base64");
  return new Uint8Array(bin);
}

const rawPub = b64ToBytes(pubB64Raw);
if (rawPub.length !== 32) {
  console.error("FAIL: public key is not 32 bytes (got", rawPub.length, ")");
  process.exit(1);
}

const sig = b64ToBytes(signature);
if (sig.length !== 64) {
  console.error("FAIL: signature is not 64 bytes (got", sig.length, ")");
  process.exit(1);
}

// ── Mirror the Worker's importEd25519PublicKey + verify ──────────────
const SPKI_PREFIX = new Uint8Array([
  0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00,
]);
const spki = new Uint8Array(SPKI_PREFIX.length + rawPub.length);
spki.set(SPKI_PREFIX, 0);
spki.set(rawPub, SPKI_PREFIX.length);

const key = await crypto.subtle.importKey(
  "spki",
  spki,
  { name: "Ed25519", namedCurve: "Ed25519" },
  false,
  ["verify"]
);
const messageBytes = new TextEncoder().encode(canon);
const ok = await crypto.subtle.verify(
  { name: "Ed25519", namedCurve: "Ed25519" },
  key,
  sig,
  messageBytes
);

if (ok) {
  console.log("OK — Worker's verifySignature round-trip works against a real ed25519 envelope.");
  process.exit(0);
} else {
  console.error("FAIL — Worker's verifySignature returned false for a valid envelope.");
  process.exit(1);
}
