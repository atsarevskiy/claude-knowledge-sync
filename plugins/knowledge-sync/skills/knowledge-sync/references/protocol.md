# Cross-machine protocol

## Scope and identity

Each configured document has a stable `id`, a `path` relative to its workspace, and a `kind` (`memory` or `steering`). IDs, not paths, align a document across machines. Do not configure credentials, SSH settings, operating-system paths, hostnames, or machine-local operating instructions as shared documents.

## Local environment discovery

`init` runs `discover-environment` by default. It creates `.knowledge-sync/environment.json`, a local inventory of candidate tools (verified with `PATH`) and aggregate command-name counts observed in local shell history and, when available, Claude Code JSONL transcripts. It deliberately discards all command arguments, prompts, assistant text, hostnames, paths, and credentials while reading those sources. It is not a sync document and `export` never reads it.

The local configuration can adjust discovery without affecting peers:

```json
"environment_discovery": {
  "tool_candidates": ["claude", "git", "kubectl", "ssh", "rsync"],
  "history_files": ["~/.zsh_history"],
  "include_claude_history": true
}
```

Refresh the profile with `discover-environment` after changing local tools or workflow. Existing profiles are copied to `.knowledge-sync/environment-snapshots/` first, and those archives are never transferred. Treat the result as an operational hint, not proof that a command is safe or authorized to run.

`<common>` is opt-in. Nested `<common>` blocks and unclosed scope tags are configuration errors. Machine labels are intentionally not interpreted as share permissions: every non-`common` block is retained only on its own machine.

## Machine-specific fact memory

Put named local decisions inside a machine scope using the same fact marker as shared facts. `check` records each one in the local `.knowledge-sync/machine-facts.json` with its document, scope, digest, and first/last-seen times. The file retains inactive historical entries instead of deleting them. Before treating a finding as new or proposing that it become shared, inspect this record: a matching active local fact is already a deliberate environment-specific decision.

`.knowledge-sync/sync-state.json` separately appends local sync/export/merge/pull metadata. Both metadata files are deliberately absent from export bundles, so every machine owns and maintains its own history.

## Analysis provenance and disappearance detection

`.knowledge-sync/document-state.json` retains the hash history of every configured memory and steering document and records the current analysis hash. `.knowledge-sync/fact-provenance.json` links each fact body to the hashes of documents that produced it, including any explicit `derived-from` references added by fact mining. If a formerly present document is absent, its state is marked missing and surfaced by `status` and `analysis-status`; no document, fact, or historical hash is deleted automatically.

## Transfer

The remote workspace is optional. If omitted, it is `.claude-knowledge-sync` relative to the remote login directory. Use an explicit workspace only when synchronizing a particular repository's memory or steering files, because SSH has no reliable way to infer the desired remote repository from a hostname alone.

`sync --peer <name>` transfers generated JSON bundles with `rsync -az -e ssh` and invokes the installed remote skill with SSH. The bundle contains only the common fragments, document IDs, hashes, source machine name, and timestamps. It contains no full document, snapshots, configuration, or local-only fragments. The peer configuration (including host and workspace path) stays in its own local `.knowledge-sync/config.json` and is never transferred.

Suggested manual exchange:

```bash
# On A. This runs A -> B and B -> A.
python3 .claude/skills/knowledge-sync/scripts/knowledge_sync.py sync --peer B
```

Repeat in the other direction. Since merge is additive and deduplicates identical fragments, repeating a bundle is safe.

## Fact merge rules

Portable facts must use this exact form inside `<common>`:

```markdown
<!-- knowledge-sync:fact name="stable fact name" -->
Fact body.
<!-- /knowledge-sync:fact -->
```

If a fact name exists only on one machine, it is imported. If both bodies are byte-for-byte identical after whitespace normalization, no change is made. If they differ, the receiver adds a conflict block containing both versions. The original fact remains unchanged. The AI should attempt a non-lossy clarified third fact only after a snapshot; it must retain the historical conflict and audit trail even if a merged formulation is added.

## Snapshot and recovery

Snapshots are stored below `.knowledge-sync/snapshots/<UTC-timestamp>/`, with a manifest and copies of configured documents. Logs are append-only JSON Lines in `.knowledge-sync/log.jsonl`. Rollback copies the selected snapshot into the current document only after taking a new snapshot, so no version is lost.
