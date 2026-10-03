# Memory-share workflow

A two-party protocol for sharing memory slices between A2A peers. Each
side explicitly approves; nothing is auto-written.

## Threat model

Memory is sensitive. A peer agent's prompt-injection filter
(`plugins/platforms.a2a.security`) treats all inbound task text as
untrusted, so it will never silently write what it receives. This
document describes the layer on top of that: explicit consent from both
humans.

## The flow

```
┌──────────┐                              ┌──────────┐
│ .2 user  │                              │ .3 user  │
│   (you)  │                              │  (them)  │
└────┬─────┘                              └────┬─────┘
     │                                         │
     │ 1. "Share my MEMORY.md with .3"         │
     ▼                                         │
plugin approval gate (memory_share detected)   │
     │                                         │
user on .2 approves                            │
     │                                         │
     │ 2. a2a_bridge_send                      │
     ▼                                         │
┌──────────┐   JSON-RPC message/send    ┌─────┴──────┐
│ .2 agent │ ──────────────────────────▶│  .3 agent  │
│          │                            │            │
│ sends    │                            │ inbound    │
│ payload  │                            │ filter    │
│ + self-  │                            │ triggers  │
│ approval │                             │
     ▲                            │ previews  │
     │                            │ diff to   │
     │                            │ .3 user   │
     │                            ▼           │
     │                       .3 user         │
     │                       approves        │
     │                       ▼               │
     │              .3 writes entry to its own MEMORY.md
     │              (with source: peer desktop, task_id, summary)
     │                            │           │
     │ 3. a2a_history pulls the   │           │
     │    reply; user sees what   │           │
     │    .3 wrote                ▼           │
     │                                       │
     ▼                                       ▼
   both audit logs record the exchange
```

## What gets shared

The sending agent (`desktop` in the example) reads a slice of its own
`MEMORY.md` and packages it as a JSON object with provenance:

```json
{
  "kind": "memory_slice",
  "from_peer": "desktop",
  "to_peer": "ai386.3",
  "slice_name": "project-alpha",
  "sent_at": "2026-10-03T13:50:00Z",
  "task_id": "task-...",
  "contents": [
    {
      "heading": "Project Alpha — 2026-10-02",
      "body": "..."
    }
  ]
}
```

The receiving agent writes the slice to its own `MEMORY.md` under a
dated, attributed heading:

```markdown
## 2026-10-03 — shared by desktop (Project Alpha slice)

<!-- source: peer=desktop → task=task-... → summary="..." -->

- Project Alpha — 2026-10-02: ...
```

## What doesn't get shared automatically

- `~/.hermes/.env` — never. Token values are redacted by the outbound
  client anyway, but explicit sharing of secrets over A2A is refused by
  the plugin's approval gate (`credential_share`).
- `~/.hermes/.hermes_history` — full chat logs are not part of memory
  exchange. If you want conversation history, use `a2a_bridge_history`
  with a specific `context_id`.
- Persona files (`SOUL.md`, `IDENTITY.md`, `USER.md`, `TOOLS.md`,
  `MEMORY.md`, `HEARTBEAT.md`) — shareable, but only on explicit
  per-file consent; never on a blanket "sync everything."

## Recommended UX prompts

When the user says "share memories," disambiguate *which* memories with
`clarify`:

- "Share everything from MEMORY.md?" → too broad; suggest a named slice
- "Share the section 'Project Alpha'?" → specific, easy to approve
- "Just share my last daily log?" → bounded and recent
- "Append a note about Project Alpha to .3's MEMORY.md?" → one-direction,
  small

For each, surface the slice name + char count + a 200-char preview, then
ask for explicit confirmation. Only after confirmation does the
`a2a_bridge_send` actually fire.

## Rolling it back

If `.3` accepts a memory slice and later decides it shouldn't have:

1. `.3`'s user calls its agent: "Remove the memory entry you wrote on
   2026-10-03 shared by desktop."
2. `.3`'s agent uses its own `memory`/`write_file` tools to edit
   `MEMORY.md`.
3. The receiving side keeps the audit log entry forever (audit logs
   are append-only and not subject to memory redaction). The audit log
   records *what was shared* even if the content was later removed.

This is by design: audit history is immutable; the live memory is
mutable. That separation is what lets the user trust the audit.

## Future

A cleaner protocol would put memory on a shared backend (e.g. an MCP
server, a git repo with signed tags, or a CRDT) that both agents read
and write through authorized tools. That's a separate plugin and is
out of scope for v0.1 of a2a-bridge.