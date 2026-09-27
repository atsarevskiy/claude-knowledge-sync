---
description: Analyze changed knowledge on both machines, then synchronize all eligible memory and steering.
argument-hint: "<peer> [--pull]"
---

Use the peer supplied in `$ARGUMENTS`; pass `--pull` unchanged when present.

First analyze every changed or unclassified local document before transferring anything. Run `analysis-status`, read every document listed in `changed` or `unclassified`, and automatically classify every existing fact file with no `metadata.scope`. Ask whether its existing content depends on tools, paths, credentials, services, configuration, or access unique to this machine. A tool name alone is portable: write `metadata.scope: common` unless the answer is clearly yes; otherwise write a local machine label. Snapshot before an edit, never delete knowledge, run `check`, then `mark-analyzed`. Re-run `analysis-status` and proceed only when `changed`, `missing`, and `unclassified` are empty.

Then run `sync --peer <peer> [--pull]`. It provisions and invokes Claude Code on the peer to perform the same changed-document analysis there. It checks both analysis states before `rsync -az -e ssh` transfers filtered bundles. It must fail explicitly—never report a successful no-op—when analysis remains pending, a document is missing, the remote Claude CLI cannot run, or neither endpoint has eligible shared knowledge.

Report local and peer `scope_counts` (common/local/unclassified), every automatic scope change, eligible items in each direction, snapshots, merges, conflicts, and any block. Do not delete facts, steering, snapshots, or logs.
