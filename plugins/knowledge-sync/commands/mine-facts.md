---
description: Analyze changed memories and steering documents, then record sourced facts.
argument-hint: "[--all]"
---

Use the active Claude model to mine facts from the configured memory and steering documents. First run `analysis-status` with the knowledge-sync script and inspect its JSON. If any document is marked missing, report it prominently and do not recreate, delete, or infer its contents.

Analyze every document whose hash differs from `last_analyzed_hash`; if `$ARGUMENTS` contains `--all`, analyze every present configured document. Read the complete relevant memories and steering docs before deriving facts. Before editing, create a snapshot with reason `before fact mining`.

Add only concise, durable facts. Use this format:

```markdown
<!-- knowledge-sync:fact name="stable fact name" -->
Fact text.
<!-- knowledge-sync:derived-from document="memory" hash="SOURCE_DOCUMENT_SHA256" -->
<!-- /knowledge-sync:fact -->
```

Each fact must cite every document hash materially used to derive it. Keep environment-specific facts inside that machine's scope. Put a fact in `<common>` only when it is portable, contains no secret or machine-local detail, and will help peers; create a `<common>` block only when adding such a fact, never as an empty scaffold. Never remove or rewrite an existing fact or source record. After edits, run `check`, then `mark-analyzed`, and report the added facts, their source documents, portable classifications, and any missing documents.
