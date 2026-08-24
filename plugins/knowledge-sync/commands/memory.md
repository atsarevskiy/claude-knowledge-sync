---
description: Synchronize only memory documents with a configured peer.
argument-hint: "<peer> [--pull]"
---

Run the knowledge-sync script for memory documents only. Use the peer supplied in $ARGUMENTS; pass `--pull` unchanged when present. First run `check --kind memory`, then run `sync --peer <peer> --kind memory [--pull]`. Explain the snapshot, merge, and conflict result without deleting any knowledge.
