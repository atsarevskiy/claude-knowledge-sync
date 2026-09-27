---
description: Analyze changed knowledge on both machines, then synchronize all eligible memory and steering.
argument-hint: "<peer> [--pull]"
---

Use the peer supplied in `$ARGUMENTS`; pass `--pull` unchanged when present.

First analyze every changed local document before transferring anything. Run `analysis-status`, read every document listed in `changed`, and apply the fact-mining rules: for each new fact ask whether it depends on tools, paths, credentials, services, configuration, or access unique to this machine. A tool name alone is portable. Snapshot before an edit, never delete knowledge, run `check`, then `mark-analyzed`. Re-run `analysis-status` and proceed only when `changed` and `missing` are empty.

Then run `sync --peer <peer> [--pull]`. It provisions and invokes Claude Code on the peer to perform the same changed-document analysis there. It checks both analysis states before `rsync -az -e ssh` transfers filtered bundles. It must fail explicitly—never report a successful no-op—when analysis remains pending, a document is missing, the remote Claude CLI cannot run, or neither endpoint has eligible shared knowledge.

Report local analysis, peer analysis, number of eligible items in each direction, snapshots, merges, conflicts, and any block. Do not delete facts, steering, snapshots, or logs.
