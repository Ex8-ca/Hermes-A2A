// canonicalize.js — JSON canonicalization for signed envelopes.
//
// Rules: sort object keys lexicographically; arrays preserve order;
// primitives use JSON.stringify (no whitespace). The two sides of a
// signed submission must apply the same rules; this is the canonical
// form. Used by both the submitter (signs canonical bytes) and the
// submit handler (verifies signature over the canonical bytes).

export function canonicalize(obj) {
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) {
    return "[" + obj.map(canonicalize).join(",") + "]";
  }
  const keys = Object.keys(obj).sort();
  return (
    "{" +
    keys.map((k) => JSON.stringify(k) + ":" + canonicalize(obj[k])).join(",") +
    "}"
  );
}
