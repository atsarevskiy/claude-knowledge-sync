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

On each machine, open the target workspace in Claude Code and invoke `/knowledge-sync:knowledge-sync` to configure the local machine.

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

Each machine also keeps local-only `.knowledge-sync/machine-facts.json` and `.knowledge-sync/sync-state.json` files. They remember environment-specific decisions and that machine's sync history, so the skill does not repeatedly rediscover a fact already classified as local.
