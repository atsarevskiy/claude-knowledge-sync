# Claude Knowledge Sync

An environment-aware Claude Code skill for synchronizing and merging memories and steering documents across machines.

The skill shares only explicitly marked portable knowledge, preserves machine-specific workflows and tool profiles, records every merge, snapshots files before changes, and retains both versions of conflicting facts.

## Install from the Claude Code marketplace

After this repository is pushed to GitHub, add its marketplace and install the plugin:

```bash
claude plugin marketplace add atsarevskiy/claude-knowledge-sync
claude plugin install knowledge-sync@claude-knowledge-sync
```

For local development, replace `atsarevskiy/claude-knowledge-sync` with the local repository path in the marketplace-add command.

## Initialize and sync

On each machine, open the target workspace in Claude Code and invoke `/knowledge-sync:knowledge-sync` to configure the local machine. Initialization scaffolds missing `.claude/memory.md` and `CLAUDE.md` with safe local and shared sections, so `check` succeeds immediately.

Configure the local `.knowledge-sync/config.json` with equivalent document IDs and an SSH peer, then run:

Run the sync command from the skill after configuring the peer.

The sync workflow uses `rsync -az -e ssh` to exchange filtered bundles in both directions; it never transfers full documents, configuration, logs, snapshots, or local environment profiles.

## Shared versus local knowledge

Only `<common>` blocks are shared:

```markdown
<common>
<!-- knowledge-sync:fact name="test workflow" -->
Run focused tests before the full suite.
<!-- /knowledge-sync:fact -->
</common>

<A>
The local proxy listens on port 4318.
</A>
```

See [the plugin skill](plugins/knowledge-sync/skills/knowledge-sync/SKILL.md) for operating instructions and [the protocol](plugins/knowledge-sync/skills/knowledge-sync/references/protocol.md) for the merge protocol.

## Commands

- `/knowledge-sync:memory B` syncs only memories with peer `B`.
- `/knowledge-sync:steering B` syncs only steering documents with peer `B`.
- Add `--pull` to either command, or to `sync --peer B --pull`, to fetch and merge the peer's shared knowledge without sending local knowledge back.

The plugin defaults to both configured memory and steering documents when no kind is selected.

## Peer and pair bootstrap

Register an existing peer and verify that Python and rsync are reachable over SSH:

```bash
knowledge_sync.py add-peer --name B --ssh-target you@host --workspace /path/to/workspace
```

`--workspace` is optional. Without it, the plugin uses `.claude-knowledge-sync` in the remote account's home directory. Specify it when the peer's knowledge should live in a particular repository: SSH cannot otherwise know which remote project owns `CLAUDE.md`.

For first-time setup, use `pair`. It initializes the local workspace if needed, copies the sync script to the peer, scaffolds its documents and local state, then registers the remote peer locally:

```bash
knowledge_sync.py pair --ssh-target you@host --workspace /path/to/workspace
```

This is sufficient for normal two-way sync started from the local machine: one sync exchanges knowledge in both directions. Reverse registration is optional and only needed when you want to initiate sync from the remote machine too. Enable it explicitly with `--register-reverse --local-ssh-target user@reachable-local-host`; no local hostname is guessed.

Pair does not write a local peer entry until remote provisioning succeeds. Remote provisioning itself cannot be globally atomic across two independent machines, but it is idempotent and safe to rerun after an interruption.

Each machine also keeps local-only `.knowledge-sync/machine-facts.json` and `.knowledge-sync/sync-state.json` files. They remember environment-specific decisions and that machine's sync history, so the skill does not repeatedly rediscover a fact already classified as local.
