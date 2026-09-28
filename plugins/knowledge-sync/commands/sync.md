---
description: Analyze changed knowledge on both machines, then synchronize all eligible memory and steering.
argument-hint: "<peer> [--pull]"
---

Use the peer supplied in `$ARGUMENTS`; pass `--pull` unchanged when present.

First analyze every changed or unclassified local document before transferring anything. Run `analysis-status`, read every document listed in `changed` or `unclassified`, and automatically classify every existing fact file with no `metadata.scope`. Apply the local `classification_rules`: sensitive/private matches always stay local; otherwise ask whether content depends on tools, paths, credentials, services, configuration, or access unique to this machine. A tool name alone is portable. Snapshot before an edit, never delete knowledge, run `check`, then `mark-analyzed`.

Run `stage-peer --peer <peer>` to copy only the peer's changed or unclassified documents and hash manifest into local `~/.claude/knowledge-sync/staging/`. Files matching the built-in secret detector are withheld from staging and must remain local. Read the remaining staged copies in this session and apply the same rules. Create a complete reviewed plan with `plan-peer --peer <peer> --session <id> --scope <document-id>=<scope> ...`; show its JSON before applying. After approval, run `apply-peer --peer <peer> --session <id>`. The peer snapshots first, verifies each source still has the staged hash, skips changed files, and changes only `metadata.scope`.

Finally run `sync --peer <peer> [--pull]`. It verifies both analysis states before `rsync -az -e ssh` transfers filtered bundles. It must fail explicitly—never report a successful no-op—when analysis remains pending, a document is missing, staging changed, or neither endpoint has eligible shared knowledge.

Report local and peer `scope_counts` (common/local/unclassified), every automatic scope change, eligible items in each direction, snapshots, merges, conflicts, and any block. Do not delete facts, steering, snapshots, or logs.
