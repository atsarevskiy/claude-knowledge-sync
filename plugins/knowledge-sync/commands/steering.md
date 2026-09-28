---
description: Synchronize only steering documents with a configured peer.
argument-hint: "<peer> [--pull]"
---

Use the peer supplied in $ARGUMENTS; pass `--pull` unchanged when present. Before transfer, perform the `/knowledge-sync:mine-facts` workflow locally for changed **steering** documents only: inspect `analysis-status --kind steering`, read and classify every changed document, snapshot before edits, run `check --kind steering`, then `mark-analyzed --kind steering`. Do not mark a document analyzed without actually reading and classifying it.

Then stage the peer with `stage-peer --peer <peer> --kind steering`, inspect its staged copies locally, show a complete `plan-peer` proposal, and apply it only after approval with `apply-peer`. Then run `sync --peer <peer> --kind steering [--pull]`. It verifies both machines and refuses transfer if either endpoint remains stale or if no eligible shared content exists. Explain any explicit block, snapshot, merge, or conflict result without deleting instructions.
