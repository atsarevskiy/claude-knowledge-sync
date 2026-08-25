---
description: Analyze changed memories and steering documents, then record sourced facts.
argument-hint: "[--all]"
---

Use the active Claude model to mine facts from the configured memory and steering documents. First run:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/knowledge-sync/scripts/knowledge_sync.py" analysis-status
```

Inspect its JSON. If any document is marked missing, report it prominently and do not recreate, delete, or infer its contents.

Analyze every document whose hash differs from `last_analyzed_hash`; if `$ARGUMENTS` contains `--all`, analyze every present configured document. These are existing native Claude Markdown sources: user steering/rules, user memory if present, and project memory. Read the complete relevant memories and steering docs before deriving facts.

First classify existing per-fact memory files from frontmatter `metadata.scope`: `common` is portable and every other label is machine-local. Do not apply inline tags to these files. For free-form steering documents, classify only explicit `<common>` blocks as portable. Ignore `MEMORY.md`; it is a generated local index. Consult `~/.claude/knowledge-sync/machine-facts.json` and `fact-provenance.json`; keep a prior local classification instead of proposing the same fact again.

Only when a genuinely new durable fact is worth recording, add it to the most relevant **existing** native document. Before that edit, create a snapshot with reason `before fact mining`. Never create a substitute memory file, empty `MEMORY.md`, empty `CLAUDE.md`, or empty `<common>` section.

Add only concise, durable facts. Use this format:

```markdown
<!-- knowledge-sync:fact name="stable fact name" -->
Fact text.
<!-- knowledge-sync:derived-from document="memory" hash="SOURCE_DOCUMENT_SHA256" -->
<!-- /knowledge-sync:fact -->
```

Each derived fact must cite every document hash materially used to derive it. Set a new per-fact file's `metadata.scope: common` only when it is portable, contains no secret or machine-local detail, and will help peers; otherwise retain its existing machine label. Use `<common>` only in free-form steering. Never remove or rewrite an existing fact or source record. After edits, run `check`, then `mark-analyzed`, and report the classifications, any added facts, their source documents, portable classifications, and missing documents.
