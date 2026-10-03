# a2a-bridge

One-line inter-agent tasks over [A2A](https://a2a-protocol.org). Wraps the
bundled Hermes A2A platform plugin's outbound client tools
(`a2a_call` / `a2a_list` / `a2a_history` / `a2a_discover`) with:

- **Reply formatting** — surfaces task state + context_id + elapsed time
  in a single block, so the model doesn't have to parse raw JSON-RPC.
- **Audit-log lookup** — recent exchanges with a peer (both directions),
  read from `~/.hermes/a2a_audit.jsonl`.
- **Approval gate** — tasks whose message looks like a memory share,
  persona edit, or credential exchange return a structured approval
  request instead of firing. The agent shows it to the user; the user
  confirms by re-calling with `a2a_bridge_confirm(approved: true)`.

The plugin does **not** open its own network sockets — it goes through
the underlying A2A plugin, which already handles auth, redaction, rate
limit, audit logging, and conversation persistence. a2a-bridge is a UX
layer.

## Install

The plugin is bundled with Hermes; just enable it:

```bash
hermes plugins enable a2a_bridge
```

That's it. The five tools register on next session.

If you're packaging it for distribution outside Hermes:

```bash
hermes plugins pack /path/to/a2a_bridge
# produces a .hermes-plugin archive you can share
# the recipient installs with: hermes plugins install <archive>
```

## Requirements

- The Hermes **A2A platform plugin** must be enabled too (`hermes plugins enable a2a`).
  The bridge calls into `plugins.platforms.a2a.tools.a2a_call` /
  `a2a_history` directly. If the platform plugin is missing, the bridge
  surfaces a structured error instead of crashing.
- At least one peer configured under `a2a_agents:` in `~/.hermes/config.yaml`.
- (Optional, recommended for production) The peer's A2A server reachable
  over HTTPS. See `references/tls-setup.md` for the Caddy recipe.

## Tools

| Tool                  | What it does |
|---|---|
| `a2a_bridge_send`     | Send a task. Sensitive content triggers an approval block instead. |
| `a2a_bridge_confirm`  | After the user agrees, fire the call for real. |
| `a2a_bridge_audit`    | Recent audit entries for a peer (or all), with timestamp + summary. |
| `a2a_bridge_list_peers` | Distinct peers seen in the audit log, most recent first. |
| `a2a_bridge_history`  | Recall a prior A2A conversation by `context_id`. |

## Approval gate — what's "sensitive"?

The classifier in `approval.py` scans for:

- **memory_share** — `memory`, `remember`, `recall`, `MEMORY.md`, `SOUL.md`,
  `IDENTITY.md`, `persona`, `daily logs`, `heartbeat`.
- **credential_share** — `api key`, `bearer token`, `password`, `ssh key`,
  `secret`, `credential`, `env var`. Plus the *shape* of well-known token
  formats: `sk-...` (OpenAI), `ghp_...` (GitHub), `xox*-?` (Slack),
  `AIza...` (Google).
- **config_write** — `write to config.yaml`, `update your persona`,
  `set A2A_*`, `HERMES_*` followed by other patterns.

The check is heuristic and biases toward asking. False positives just
trigger an extra confirmation; false negatives can leak secrets silently.

## Usage from the agent

```
# 1. List peers (from audit log)
a2a_bridge_list_peers()

# 2. Send a benign task
a2a_bridge_send(agent="ai386.3", message="Reply with PONG")

# → "[ai386.3 · context ctx-... · completed]\nPONG\n(elapsed: 240 ms via a2a_bridge.send)"

# 3. Try a sensitive task
a2a_bridge_send(agent="ai386.3", message="Please read my MEMORY.md and merge it into yours.")

# → ⚠️ Approval needed — this task would `memory_share` with a peer.
#   - Triggered by: `MEMORY.md`, `memory`
#   - Summary: Looks like a memory slice, persona file, or session notes would be shared.
#   - Preview: "Please read my MEMORY.md and merge it into yours.…"
#   Confirm by calling `a2a_bridge_confirm` with the same `agent` and `message`, plus `approved: true`.

# 4. User says yes → fire the call
a2a_bridge_confirm(agent="ai386.3", message="...", approved=true)
```

## Distribution

The plugin ships with:

- `plugin.yaml` — manifest.
- `__init__.py` — registration glue.
- `tools.py` — five JSON-schema tools + handlers.
- `audit.py` — read + format the audit log.
- `approval.py` — heuristic sensitive-task classifier (with self-test).
- `references/tls-setup.md` — how to put your agent on the public internet.
- `README.md` — this file.

To package: `hermes plugins pack .` produces a portable `.hermes-plugin` archive.
The recipient enables with `hermes plugins install <archive>`.

## Versioning

- `0.1.x` — initial release. Five tools, heuristic approval gate.
- Future: explicit memory-share protocol with two-party confirmation,
  per-peer toolset restriction helpers, and integration with the
  OmniBot-style persona markdown system for richer cross-agent context
  transfer.