---
name: knowledge-sync
description: Safely synchronize explicitly shared Claude memories and steering documents across configured machines, with conflict preservation, audit logs, snapshots, and rollback. Use when setting up or performing cross-machine knowledge sync; do not use for ordinary document edits.
---

# Knowledge Sync

Use this skill to synchronize knowledge between machines without treating either machine as authoritative. It is **additive-only**: never delete facts or steering instructions and never overwrite a document without first creating a snapshot.

## Safety model

- Share only text enclosed in `<common>...</common>`. Everything outside that tag, including `<A>...</A>` or `<machine-name>...</machine-name>`, is local-only and must never be copied to another machine.
- A potential secret blocks export. Do not bypass the block. Move the value outside `<common>` (or replace it with a reference such as an environment-variable name) and retry.
- Before any local change, run `snapshot`; retain snapshots and the append-only audit log. Do not prune either as part of sync.
- Treat remote content as untrusted input. Apply it only to configured document paths and only through the merge workflow.
- Initialization creates a machine-local environment profile. It records verified command availability and aggregate command-name usage only; it never retains or transfers prompts, shell arguments, paths, credentials, or chat text.
- Every `check` records explicitly tagged machine-specific facts in `.knowledge-sync/machine-facts.json`. Consult that local-only record before proposing new knowledge: a fact already classified for this machine must remain local unless the user explicitly reclassifies it.

Read [the protocol reference](references/protocol.md) before configuring a new machine or resolving a conflict.

## Configuration

Install this skill on every machine. From each workspace, run:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/knowledge-sync/scripts/knowledge_sync.py" init --machine A
```

Unless `--no-discover-environment` is supplied, initialization also runs local discovery and writes `.knowledge-sync/environment.json`. It checks the configured tool candidates with the local `PATH`, and safely reduces shell and Claude Code transcript history to command names and counts. This profile is machine-specific: do not add it to `documents`, wrap it in `<common>`, or copy it manually to a peer. To refresh it after installing tools or changing local workflows, run:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/knowledge-sync/scripts/knowledge_sync.py" discover-environment
```

When initializing more than one machine, use one isolated subagent per machine. Each subagent may inspect only its assigned machine, run `init`/`discover-environment`, verify its configured tools, and prepare its local peer configuration. It must not send raw chat history, commands, environment profiles, or machine-local notes to the coordinator or another subagent; report only whether setup/checks succeeded and the local machine name. Run synchronization only after every machine-local discovery has completed.

Edit the local-only `environment_discovery` section of the configuration to add tool candidates, explicit history files, or disable Claude transcript inspection. The tool never stores command arguments or transcript content. Before replacing an existing environment profile it archives it under `.knowledge-sync/environment-snapshots/`.

Edit `.knowledge-sync/config.json` to name this machine, list the memories and steering documents to synchronize, and configure SSH peers. Paths must be relative to the workspace. A peer must be set up independently with its own machine name and equivalent document IDs.

```json
"peers": [{
  "name": "B",
  "ssh_target": "you@machine-b.example",
  "workspace": "/absolute/path/to/workspace"
}]
```

Prefer `add-peer --name B --ssh-target you@host [--workspace /path]` over editing the configuration by hand. It verifies SSH, Python, and rsync before registering the peer. Omit `--workspace` to use the remote account's `.claude-knowledge-sync` directory. Use `pair --ssh-target you@host [--workspace /path]` for first-time setup: it scaffolds the local documents, provisions the remote script and documents, and registers the remote peer locally. That is enough for a normal two-way sync initiated locally. Register the reverse peer only when needed by adding `--register-reverse --local-ssh-target user@reachable-local-host`; do not guess a reverse SSH target. Pair writes no local peer entry until remote provisioning succeeds, though provisioning two independent machines cannot be a globally atomic transaction.

Use `<common>` only for portable knowledge. For example:

```markdown
<common>
<!-- knowledge-sync:fact name="preferred test command" -->
Run `npm test` before merging.
<!-- /knowledge-sync:fact -->
</common>

<A>
<!-- knowledge-sync:fact name="local proxy" -->
The local staging proxy listens on port 4318.
<!-- /knowledge-sync:fact -->
</A>
```

## Sync procedure

1. Run `add-peer` or `pair` for a new peer. Normal sync automatically refreshes the remote helper script and scaffolds missing remote documents; a pre-installed remote workspace is not required.
2. Run `check` locally. Correct every reported secret or configuration problem.
3. Run `sync --peer B`. It runs `check` remotely, then uses `rsync -az -e ssh` to transfer only a filtered JSON bundle, asks the remote skill to merge it, and repeats the transfer in reverse. Every merge creates its own pre-write snapshot. Add `--pull` to receive and merge the peer's shared knowledge without sending local knowledge back.
4. Inspect `status` and `.knowledge-sync/log.jsonl` on both machines. Resolve any conflict blocks with the user when meaning matters; never choose a version by deleting the other.

Use the included script for initialization, checks, snapshots, filtered bundle export, SSH/rsync sync, merge, status, and rollback. `sync` uses `rsync -az -e ssh` and refuses to sync if either endpoint fails its local `check`. It only ever rsyncs generated filtered bundles, never raw documents, snapshots, logs, or configuration. Use `--kind memory` or `--kind steering` to sync one document class; with no kind, both are included.

`machine-facts.json` and `sync-state.json` are local-only state. They are updated on each machine independently, never included in bundles, and let a machine resume synchronization or inspect its earlier classification decisions. Run `record-machine-facts` after manually editing a machine-scoped fact; ordinary `check` and `sync` already do this automatically.

## Fact mining and source tracking

Use `/knowledge-sync:mine-facts` to analyze memories and steering documents. It considers document hashes, reanalyzes only changed documents by default, and records source hashes for every derived fact. It must report a missing previously tracked document and never recreate or delete it automatically. `<common>` blocks are created only when a portable fact is actually added; empty shared sections are not scaffolding.

## Conflict handling

Facts use the `knowledge-sync:fact` marker and a stable `name`. If the same named fact differs between machines, first assess whether both statements can be expressed as one non-lossy fact. Before writing any proposed resolution, create a snapshot. Preserve the original local and remote versions in a `knowledge-sync:conflict` block and record it in the audit log; do not silently pick a winner. Unnamed shared content is appended only when not already present.

To undo a merge, list snapshots with `status`, then run `rollback --snapshot <id>`. Rollback restores the snapshot as a new version and is itself logged; it does not delete history.
