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
- `~/.claude/projects/<project>/memory/**/*.md`, including Claude Code's `MEMORY.md` entrypoint and topic files.

Only `~/.claude/knowledge-sync/` is created, for this plugin's metadata. `init` is valid even when no native document exists. When a document is later discovered, it is added to the configuration; when one disappears, it is reported as missing rather than recreated or removed.

## Portable and local facts

Native files remain normal Markdown. Make a fact portable only with an explicit `<common>` block:

```markdown
<common>
<!-- knowledge-sync:fact name="test workflow" -->
Run focused tests before the full suite.
<!-- /knowledge-sync:fact -->
</common>
```

Everything else remains local. Mining derives a fact's default scope from the file in which it already lives (`project-memory`, `user-memory`, `user-steering`, or `user-rule`) and records its source document hash. It never moves, replaces, or erases existing memory.

## Bootstrap and sync

```bash
knowledge_sync.py init --machine A
knowledge_sync.py add-peer --name B --ssh-target you@host
# Or provision the remote helper and add the peer in one direction:
knowledge_sync.py pair --ssh-target you@host
```

`pair` copies only the helper and creates its `~/.claude/knowledge-sync/` metadata directory remotely. It discovers the remote machine's existing native documents; it never scaffolds empty memory or `CLAUDE.md` files. Reverse registration is optional and needs an explicit reachable local SSH target.

The exchange uses `rsync -az -e ssh` for filtered bundles and SSH to run the helper. Full documents, configuration, logs, snapshots, environment profiles, and local-only sections are never transferred.

- `/knowledge-sync:memory B` syncs discovered memories.
- `/knowledge-sync:steering B` syncs discovered steering files.
- Add `--pull` for one-way receive-and-merge.

With no kind option, both are selected. See [the skill](plugins/knowledge-sync/skills/knowledge-sync/SKILL.md) and [protocol](plugins/knowledge-sync/skills/knowledge-sync/references/protocol.md).
