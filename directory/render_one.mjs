// render_one.mjs — render a single agent's profile page.
// Usage: node render_one.mjs <template_path> <entry_json_path> <output_path>
// Reads the entry JSON from disk (no shell-escape gotchas) and writes
// the rendered HTML to output_path. Used by render_agents.py.

import { readFileSync, writeFileSync } from "node:fs";
import { render } from "./render.mjs";

const [, , templatePath, entryPath, outputPath] = process.argv;
if (!templatePath || !entryPath || !outputPath) {
  console.error("usage: node render_one.mjs <template> <entry.json> <output.html>");
  process.exit(2);
}

const template = readFileSync(templatePath, "utf8");
const entry = JSON.parse(readFileSync(entryPath, "utf8"));
const html = render(template, entry);
writeFileSync(outputPath, html, "utf8");
