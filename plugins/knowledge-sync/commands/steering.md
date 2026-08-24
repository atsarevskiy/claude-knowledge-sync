---
description: Synchronize only steering documents with a configured peer.
argument-hint: "<peer> [--pull]"
---

Run the knowledge-sync script for steering documents only. Use the peer supplied in $ARGUMENTS; pass `--pull` unchanged when present. First run `check --kind steering`, then run `sync --peer <peer> --kind steering [--pull]`. Explain the snapshot, merge, and conflict result without deleting any steering instruction.
