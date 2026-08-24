"""Native Claude document discovery and local knowledge-sync state."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
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
        if not path.is_file():
            continue
        relative = str(path.relative_to(CLAUDE_HOME))
        found.append({"id": hashlib.sha256(relative.encode()).hexdigest()[:16],
                      "path": str(path), "kind": kind, "scope": scope})
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
