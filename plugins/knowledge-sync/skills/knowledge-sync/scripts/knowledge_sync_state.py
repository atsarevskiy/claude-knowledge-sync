"""Native Claude document discovery and local knowledge-sync state."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
STATE = CLAUDE_HOME / "knowledge-sync"
CONFIG = STATE / "config.json"
LOG = STATE / "log.jsonl"
ENVIRONMENT = STATE / "environment.json"
MACHINE_FACTS = STATE / "machine-facts.json"
SYNC_STATE = STATE / "sync-state.json"
DOCUMENT_STATE = STATE / "document-state.json"
FACT_PROVENANCE = STATE / "fact-provenance.json"
LEGACY_ALWAYS_LOCAL_PATTERNS = ["(?i)\\b(secret|credential|password|token|private|confidential)\\b"]
DEFAULT_ALWAYS_LOCAL_PATTERNS = [
    "(?i)\\b(?:password|passphrase|secret(?:[_ -]?key)?|api[_ -]?key|access[_ -]?token|auth[_ -]?token)\\s*[:=]\\s*['\\\"]?[A-Za-z0-9_./+=-]{12,}"
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def doc_path(doc: dict) -> Path:
    return Path(doc["path"]).expanduser()


def is_memory_index(path: Path) -> bool:
    """Claude manages MEMORY.md as an index; it is never a sync source."""
    return path.name.lower() == "memory.md" and path.parent.name == "memory"


def frontmatter_scope(text: str) -> str | None:
    """Read the small YAML subset used by Claude fact files: metadata.scope."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None
    lines = text.splitlines()
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() in {"---", "..."})
    except StopIteration:
        return None
    frontmatter = "\n".join(lines[1:end])
    metadata = re.search(r"(?ms)^metadata\s*:\s*\n((?:^[ \t]+.*(?:\n|$))*)", frontmatter)
    if not metadata:
        return None
    scope = re.search(r"(?m)^[ \t]+scope\s*:\s*['\"]?([A-Za-z][A-Za-z0-9_-]*)['\"]?\s*(?:#.*)?$", metadata.group(1))
    return scope.group(1) if scope else None


def native_documents() -> list[dict]:
    """Discover existing Claude Code memory and instruction Markdown files."""
    candidates = [(CLAUDE_HOME / "CLAUDE.md", "steering", "user-steering")]
    for folder, kind, scope in (
        (CLAUDE_HOME / "rules", "steering", "user-rule"),
        (CLAUDE_HOME / "memory", "memory", "user-memory"),
        (CLAUDE_HOME / "projects", "memory", "project-memory"),
    ):
        if not folder.is_dir():
            continue
        pattern = "*/memory/**/*.md" if folder.name == "projects" else "**/*.md"
        candidates.extend((path, kind, scope) for path in folder.glob(pattern))
    found = []
    for path, kind, scope in candidates:
        if not path.is_file() or is_memory_index(path):
            continue
        relative = str(path.relative_to(CLAUDE_HOME))
        document = {"id": hashlib.sha256(relative.encode()).hexdigest()[:16],
                    "path": str(path), "kind": kind, "scope": scope}
        if kind == "memory":
            document["mode"] = "fact-file"
            document["frontmatter_scope"] = frontmatter_scope(path.read_text(errors="replace"))
        else:
            document["mode"] = "free-form"
        found.append(document)
    return sorted(found, key=lambda item: item["path"])


def write_config(config: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, indent=2) + "\n")


def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit("No ~/.claude/knowledge-sync/config.json. Run init first.")
    try:
        config = json.loads(CONFIG.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid config: {exc}") from exc
    if not isinstance(config.get("machine"), str) or not config["machine"]:
        raise SystemExit("config.machine must be a non-empty string")
    if not isinstance(config.get("documents"), list):
        raise SystemExit("config.documents must be a list")
    for doc in config["documents"]:
        path = Path(doc.get("path", "")).expanduser()
        if not isinstance(doc.get("id"), str) or not is_under(path, CLAUDE_HOME):
            raise SystemExit("Every document must be an existing or historical path below ~/.claude")
    for peer in config.get("peers", []):
        if not all(isinstance(peer.get(key), str) and peer[key] for key in ("name", "ssh_target")):
            raise SystemExit("Every peer needs non-empty name and ssh_target fields")
    return config


def refresh_documents(config: dict) -> None:
    """Add documents found now but retain historical entries for disappearance alerts."""
    current = {item["id"]: item for item in config.get("documents", [])}
    rules = config.get("classification_rules")
    if isinstance(rules, dict) and rules.get("always_local_patterns") == LEGACY_ALWAYS_LOCAL_PATTERNS:
        rules["always_local_patterns"] = DEFAULT_ALWAYS_LOCAL_PATTERNS
    for item in current.values():
        if is_memory_index(doc_path(item)):
            item["generated_index"] = True
            item["syncable"] = False
    for doc in native_documents():
        current[doc["id"]] = doc
    config["documents"] = list(current.values())
    write_config(config)


def read_json(path: Path, fallback: dict) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else fallback
    except json.JSONDecodeError:
        return fallback


def log(event: str, **data: object) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as stream:
        stream.write(json.dumps({"time": now(), "event": event, **data}, sort_keys=True) + "\n")
