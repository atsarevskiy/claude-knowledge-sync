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

First classify existing per-fact memory files from frontmatter `metadata.scope`: `common` is portable and every other label is machine-local. For every existing fact file with no `metadata.scope`, classify it now and write the result; do not leave it unscoped. Do not apply inline tags to these files. For free-form steering documents, classify only explicit `<common>` blocks as portable. Ignore `MEMORY.md`; it is a generated local index. Consult `~/.claude/knowledge-sync/machine-facts.json` and `fact-provenance.json`; keep a prior explicit local classification instead of proposing the same fact again.

For every existing unscoped file and every **new candidate fact**, explicitly apply this question before choosing its scope: **“Does this depend on tools, paths, credentials, services, configuration, or access that exist only on this one machine?”** Mark it machine-local only when the answer is clearly yes from the source or user context. A generic tool reference is not enough: `git`, `node`, `python`, `kubectl`, a test runner, or any other tool may be shared when it is available on both machines. If the only reason to exclude a fact is a tool name, classify it as `common` instead of skipping it. Preserve the secret check: secrets must never become common.

Only when a genuinely new durable fact is worth recording, add it to the most relevant **existing** native document. Before that edit, create a snapshot with reason `before fact mining`. Never create a substitute memory file, empty `MEMORY.md`, empty `CLAUDE.md`, or empty `<common>` section.

Add only concise, durable facts. Use this format:

```markdown
<!-- knowledge-sync:fact name="stable fact name" -->
Fact text.
<!-- knowledge-sync:derived-from document="memory" hash="SOURCE_DOCUMENT_SHA256" -->
<!-- /knowledge-sync:fact -->
```

Each derived fact must cite every document hash materially used to derive it. Set a new per-fact file's `metadata.scope: common` when that dependency question is not clearly yes, the fact has no secret, and it will help peers; otherwise use the existing machine label. Use `<common>` only in free-form steering. Never remove or rewrite an existing fact or source record. After edits, run `check`, then `mark-analyzed`, and report the classifications, the answer to the dependency question for each new fact, any added facts, their source documents, portable classifications, and missing documents.
