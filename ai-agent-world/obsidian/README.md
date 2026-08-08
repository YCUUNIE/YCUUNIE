# AI-Agents — bundled demo vault

This folder is the default Obsidian vault used when
`config/config.yaml → obsidian.vault_path` is empty. Open it in Obsidian, or
point the app at your own vault instead.

The agents populate it **at runtime**:

- `Agents/` — one profile note per agent (written on startup).
- `Memories/<Agent>/` — long-term memories, one Markdown note each.
- `Conversations/`, `Tasks/`, `Workflows/`, `World/`, `Research/`, `Logs/`.
- `System/Repairs/` — a report for every self-healing action.

Notes use YAML frontmatter so they're first-class in Obsidian (tags,
Dataview, graph view, etc.). The example files here show the shapes; the app
adds many more as the world runs.

Access is sandboxed: agents can only read/write inside this vault.
