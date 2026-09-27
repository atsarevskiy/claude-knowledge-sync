# Claude Knowledge Sync

An environment-aware Claude Code skill that synchronizes and non-destructively merges knowledge across machines. It uses Claude Code's actual memory and instruction files, shares explicitly portable facts, preserves conflicts, and keeps snapshots, audit logs, and classification history.

## Install from the Claude Code marketplace

```bash
claude plugin marketplace add atsarevskiy/claude-knowledge-sync
claude plugin install knowledge-sync@claude-knowledge-sync
```

## Native Claude Code documents

Knowledge Sync does not create a competing memory or steering document. It discovers existing Markdown files in:

- `~/.claude/CLAUDE.md` and `~/.claude/rules/**/*.md` for user steering;
- `~/.claude/memory/**/*.md`, if a user maintains that location;
- `~/.claude/projects/<project>/memory/*.md` fact files. `MEMORY.md` is a generated local index and is deliberately excluded from sync.

Only `~/.claude/knowledge-sync/` is created, for this plugin's metadata. `init` is valid even when no native document exists. When a document is later discovered, it is added to the configuration; when one disappears, it is reported as missing rather than recreated or removed.

## Portable and local facts

Per-fact memory files use Claude's frontmatter scope. A whole fact file is portable when `metadata.scope` is `common`; any other label keeps it local:

```markdown
---
metadata:
  scope: common
---

# Test workflow
Run focused tests before the full suite.
```

For free-form steering documents such as `CLAUDE.md` and rule files, use inline `<common>` blocks. Mining reads and records the fact-file scope plus source hash. It never moves, replaces, or erases existing memory.

## Bootstrap and sync

```bash
knowledge_sync.py init --machine A
knowledge_sync.py add-peer --name B --ssh-target you@host
# Or provision the remote helper and add the peer in one direction:
knowledge_sync.py pair --ssh-target you@host
```

`pair` copies only the helper and creates its `~/.claude/knowledge-sync/` metadata directory remotely. It discovers the remote machine's existing native documents; it never scaffolds empty memory or `CLAUDE.md` files. Both machines need the `claude` CLI: sync invokes Claude on the peer to analyze changed documents before transfer. Reverse registration is optional and needs an explicit reachable local SSH target.

The exchange uses `rsync -az -e ssh` for filtered bundles and SSH to run the helper. Full documents, configuration, logs, snapshots, environment profiles, and local-only sections are never transferred.

- `/knowledge-sync:memory B` syncs discovered memories.
- `/knowledge-sync:steering B` syncs discovered steering files.
- Add `--pull` for one-way receive-and-merge.

With no kind option, both are selected. `/knowledge-sync:sync B` analyzes changed documents on both machines before transfer and fails loudly if analysis remains pending or no eligible shared knowledge exists. See [the skill](plugins/knowledge-sync/skills/knowledge-sync/SKILL.md) and [protocol](plugins/knowledge-sync/skills/knowledge-sync/references/protocol.md).
