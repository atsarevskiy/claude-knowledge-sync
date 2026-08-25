# Cross-machine protocol

## Native sources and identity

Knowledge Sync discovers existing Claude Code Markdown below `~/.claude`:

- `CLAUDE.md` and `rules/**/*.md` are steering;
- `memory/*.md`, if present, is per-fact user memory;
- `projects/<project>/memory/*.md` is per-fact project memory. `MEMORY.md` is a generated index and is excluded.

Every source has a stable ID derived from its path relative to `~/.claude`. The plugin creates no native memory or steering file. Its only owned directory is `~/.claude/knowledge-sync/`.

## Scope, mining, and history

For a per-fact memory file, `metadata.scope` in YAML frontmatter is authoritative: `common` makes the whole file portable; any other label keeps it local. Free-form steering uses explicit `<common>` blocks. `machine-facts.json` retains source and scope classifications, so an already-local decision is not repeatedly treated as new.

`document-state.json` stores each source's hash history. `fact-provenance.json` links tagged facts to the document hash that produced them and any validated `derived-from` references. If a previously known document is absent, it is marked missing and surfaced by analysis/status. No file, fact, source hash, or provenance record is deleted automatically.

## Transfer

`sync --peer <name>` copies the remote helper into the peer's `~/.claude/knowledge-sync/bin/`, then transfers generated bundles with `rsync -az -e ssh` and calls the helper over SSH. A peer contains only a name and SSH target: no workspace path is used.

Bundles contain shared fragments, source machine, document IDs, and hashes. They exclude full documents, configuration, snapshots, audit logs, environment discovery, and all local-only content. `--pull` performs receive-and-merge only.

## Merge and recovery

Portable free-form content uses the following additive form:

```markdown
<common>
<!-- knowledge-sync:fact name="stable fact name" -->
Fact body.
<!-- /knowledge-sync:fact -->
</common>
```

A portable per-fact memory file instead uses `metadata.scope: common` and transfers as one file. If it is absent on the receiver, Knowledge Sync creates that shared fact file under the matching native memory path after a snapshot. It never transfers, overwrites, or regenerates `MEMORY.md`.

An unknown fact is appended. An identical normalized fact is deduplicated. A conflicting same-named fact creates a block that retains local and remote versions; the original is never overwritten or deleted. Any assistant-created clarification must preserve both source versions and follow a snapshot.

Snapshots live in `~/.claude/knowledge-sync/snapshots/<UTC-timestamp>/`; JSONL audit logs are at `~/.claude/knowledge-sync/log.jsonl`. Rollback first snapshots the current document, then restores the selected historical version.
