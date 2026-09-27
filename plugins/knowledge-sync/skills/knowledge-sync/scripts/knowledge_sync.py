#!/usr/bin/env python3
"""Additive, local-only support for the knowledge-sync Claude Code skill."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import socket
from pathlib import Path
from collections import Counter
from knowledge_sync_state import (
    CLAUDE_HOME, CONFIG, DOCUMENT_STATE, ENVIRONMENT, FACT_PROVENANCE, LOG,
    MACHINE_FACTS, STATE, SYNC_STATE, doc_path, load_config, log,
    frontmatter_scope, is_memory_index, is_under, native_documents, now, read_json,
    refresh_documents, write_config,
)

REMOTE_SCRIPT = ".claude/knowledge-sync/bin/knowledge_sync.py"
FACT = re.compile(r'<!-- knowledge-sync:fact name="([^"]+)" -->\s*(.*?)\s*<!-- /knowledge-sync:fact -->', re.S)
COMMON = re.compile(r"<common>\s*(.*?)\s*</common>", re.S)
SCOPE = re.compile(r"<([A-Za-z][A-Za-z0-9_-]*)>\s*(.*?)\s*</\1>", re.S)
DERIVED_FROM = re.compile(r'<!-- knowledge-sync:derived-from document="([^"]+)" hash="([0-9a-f]{64})" -->')
SECRET = re.compile(r"(?:AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_-]?key|secret|token|password)\s*[:=]\s*[^\s]{8,}|gh[pousr]_[A-Za-z0-9_]{20,})", re.I)
DEFAULT_TOOL_CANDIDATES = [
    "claude", "codex", "git", "rg", "python3", "node", "npm", "bun", "go",
    "docker", "kubectl", "helm", "terraform", "ansible", "ssh", "rsync",
]
SHELL_OPERATORS = {"|", "||", "&&", ";", "&", "("}


def ensure_documents(config: dict) -> None:
    """Kept for compatibility: native Claude documents are never scaffolded."""


def scoped_config(config: dict, kind: str | None) -> dict:
    if kind is not None and kind not in {"memory", "steering"}:
        raise SystemExit("--kind must be memory or steering")
    selected = [doc for doc in config["documents"]
                if doc.get("syncable", True) and not doc.get("generated_index")
                and (kind is None or doc.get("kind") == kind)]
    if kind is not None and not selected:
        raise SystemExit(f"No configured document has kind: {kind}")
    return {**config, "documents": selected}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def document_mode(doc: dict) -> str:
    return doc.get("mode", "fact-file" if doc.get("kind") == "memory" else "free-form")


def fact_file_body(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], 1):
            if line.strip() in {"---", "..."}:
                return "\n".join(lines[index + 1:]).strip()
    return text.strip()


def document_facts(doc: dict, text: str) -> list[tuple[str, str]]:
    """A per-fact memory file is itself one fact; free-form files use markers."""
    if document_mode(doc) == "fact-file":
        body = fact_file_body(text)
        return [(doc_path(doc).stem, body)] if body else []
    return FACT.findall(text)


def shareable_parts(doc: dict, text: str) -> list[str]:
    """Return exportable content according to native file format and scope."""
    if document_mode(doc) == "fact-file":
        return [text] if doc.get("frontmatter_scope") == "common" else []
    return common_parts(text)


def validate_document(doc: dict, text: str, label: str) -> list[str]:
    if document_mode(doc) != "fact-file":
        return validate_text(text, label)
    actual_scope = frontmatter_scope(text)
    if actual_scope != doc.get("frontmatter_scope"):
        return [f"{label}: frontmatter metadata.scope changed; rerun provision to refresh discovery"]
    if actual_scope == "common" and SECRET.search(text):
        return [f"{label}: possible secret in a shareable fact file"]
    return []


def scan_document_state(config: dict) -> dict:
    """Track current and historical document hashes without deleting history."""
    state = read_json(DOCUMENT_STATE, {"format": 1, "machine": config["machine"], "documents": {}})
    state["machine"] = config["machine"]
    documents = state.setdefault("documents", {})
    for doc in config["documents"]:
        path = doc_path(doc)
        previous = documents.get(doc["id"], {})
        if not path.exists():
            documents[doc["id"]] = {**previous, "path": str(path), "kind": doc.get("kind"),
                                    "missing": True, "missing_since": previous.get("missing_since", now()),
                                    "last_checked": now()}
            continue
        digest = sha256_text(path.read_text())
        history = previous.get("hash_history", [])
        if not history or history[-1].get("hash") != digest:
            history.append({"hash": digest, "seen": now()})
        documents[doc["id"]] = {"path": str(path), "kind": doc.get("kind"), "hash": digest,
                                "hash_history": history, "missing": False, "last_checked": now(),
                                "last_analyzed_hash": previous.get("last_analyzed_hash"),
                                "last_analyzed_at": previous.get("last_analyzed_at")}
    DOCUMENT_STATE.write_text(json.dumps(state, indent=2) + "\n")
    return state


def record_fact_provenance(config: dict, document_state: dict) -> None:
    """Link every present fact to the hash of its source documents."""
    state = read_json(FACT_PROVENANCE, {"format": 1, "machine": config["machine"], "facts": {}})
    state["machine"] = config["machine"]
    facts = state.setdefault("facts", {})
    active = set()
    hashes = document_state.get("documents", {})
    for doc in config["documents"]:
        source = hashes.get(doc["id"], {})
        if source.get("missing") or not source.get("hash"):
            continue
        text = doc_path(doc).read_text()
        scoped_facts = {}
        for scope, content in SCOPE.findall(text):
            for scoped_name, scoped_body in FACT.findall(content):
                scoped_facts[(scoped_name, canonical(scoped_body))] = "shared" if scope == "common" else scope
        for name, body in document_facts(doc, text):
            body_hash = sha256_text(canonical(body))
            key = f"{doc['id']}:{name}:{body_hash}"
            derived = [{"document": doc["id"], "hash": source["hash"]}]
            for source_id, source_hash in DERIVED_FROM.findall(body):
                candidate = hashes.get(source_id, {})
                known = [item.get("hash") for item in candidate.get("hash_history", [])]
                if source_hash in known:
                    derived.append({"document": source_id, "hash": source_hash})
            previous = facts.get(key, {})
            inferred_scope = ("shared" if document_mode(doc) == "fact-file"
                              and doc.get("frontmatter_scope") == "common"
                              else doc.get("scope", doc.get("kind")))
            facts[key] = {"name": name, "document": doc["id"], "fact_hash": body_hash,
                          "scope": scoped_facts.get((name, canonical(body)), inferred_scope),
                          "sources": derived, "first_seen": previous.get("first_seen", now()),
                          "last_seen": now(), "active": True}
            active.add(key)
    for key, value in facts.items():
        if key not in active:
            value["active"] = False
    FACT_PROVENANCE.write_text(json.dumps(state, indent=2) + "\n")
    log("fact_provenance_recorded", active=sum(v.get("active", False) for v in facts.values()))


def record_machine_facts(config: dict) -> None:
    """Record tagged facts with the scope implied by their native source file."""
    state = read_json(MACHINE_FACTS, {"format": 1, "machine": config["machine"], "facts": {}})
    state["machine"] = config["machine"]
    facts = state.setdefault("facts", {})
    seen = set()
    processed_ids = set()
    for doc in config["documents"]:
        processed_ids.add(doc["id"])
        path = doc_path(doc)
        if not path.exists():
            continue
        text = path.read_text()
        explicit_scopes = {}
        for scope, content in SCOPE.findall(text):
            for name, body in FACT.findall(content):
                explicit_scopes[(name, canonical(body))] = "shared" if scope == "common" else scope
        for name, body in document_facts(doc, text):
            scope = explicit_scopes.get((name, canonical(body)), doc.get("scope", doc.get("kind", "local")))
            if document_mode(doc) == "fact-file" and doc.get("frontmatter_scope") == "common":
                scope = "shared"
            if scope == "shared":
                continue
            key = f"{doc['id']}:{scope}:{name}"
            digest = hashlib.sha256(canonical(body).encode()).hexdigest()
            previous = facts.get(key, {})
            facts[key] = {
                "name": name, "document": doc["id"], "scope": scope,
                "digest": digest, "first_seen": previous.get("first_seen", now()),
                "last_seen": now(), "active": True,
            }
            seen.add(key)
    for key, value in facts.items():
        if key.split(":", 1)[0] in processed_ids and key not in seen:
            value["active"] = False
    MACHINE_FACTS.write_text(json.dumps(state, indent=2) + "\n")
    log("machine_facts_recorded", active=sum(v.get("active", False) for v in facts.values()))


def record_sync_state(config: dict, event: str, **details: object) -> None:
    """Append local-only sync metadata so either endpoint can resume independently."""
    state = read_json(SYNC_STATE, {"format": 1, "machine": config["machine"], "events": []})
    state["machine"] = config["machine"]
    state.setdefault("events", []).append({"time": now(), "event": event, **details})
    SYNC_STATE.write_text(json.dumps(state, indent=2) + "\n")


def common_parts(text: str) -> list[str]:
    if text.count("<common>") != text.count("</common>"):
        raise ValueError("unclosed <common> scope")
    parts = COMMON.findall(text)
    if any("<common>" in part or "</common>" in part for part in parts):
        raise ValueError("nested <common> scope")
    return [part.strip() for part in parts if part.strip()]


def validate_text(text: str, label: str) -> list[str]:
    problems = []
    try:
        parts = common_parts(text)
    except ValueError as exc:
        return [f"{label}: {exc}"]
    for part in parts:
        if SECRET.search(part):
            problems.append(f"{label}: possible secret in <common>; move it outside the shared scope")
    return problems


def validate_provenance(config: dict, document_state: dict) -> list[str]:
    problems = []
    documents = document_state.get("documents", {})
    for doc in config["documents"]:
        path = doc_path(doc)
        if not path.exists():
            continue
        for source_id, source_hash in DERIVED_FROM.findall(path.read_text()):
            candidate = documents.get(source_id, {})
            known = [item.get("hash") for item in candidate.get("hash_history", [])]
            if source_hash not in known:
                problems.append(f"{doc['id']}: derived-from references unknown hash for {source_id}")
    return problems


def validate_remote_part(part: object, label: str) -> list[str]:
    if not isinstance(part, str):
        return [f"{label}: common fragment must be text"]
    if "<common>" in part or "</common>" in part or SCOPE.search(part):
        return [f"{label}: remote fragment may not contain scope tags"]
    return validate_text(f"<common>\n{part}\n</common>", label)


def snapshot(config: dict, reason: str) -> str:
    snapshot_id = now()
    folder = STATE / "snapshots" / snapshot_id
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"id": snapshot_id, "reason": reason, "documents": []}
    for doc in config["documents"]:
        source = doc_path(doc)
        if source.exists():
            target = folder / doc["id"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest["documents"].append({"id": doc["id"], "path": doc["path"], "saved_as": doc["id"]})
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log("snapshot", snapshot=snapshot_id, reason=reason)
    return snapshot_id


def canonical(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines())


def conflict(name: str, local: str, remote: str, source: str) -> str:
    return ("<!-- knowledge-sync:conflict name=\"%s\" source=\"%s\" -->\n"
            "LOCAL VERSION:\n%s\n\nREMOTE VERSION:\n%s\n"
            "<!-- /knowledge-sync:conflict -->" % (name, source, local, remote))


def incoming_fact_document(incoming: dict) -> dict | None:
    """Accept only a common per-fact file in Claude's native memory folders."""
    relative = incoming.get("relative_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        return None
    path = CLAUDE_HOME / relative
    parts = Path(relative).parts
    allowed = (len(parts) >= 2 and parts[0] == "memory") or (
        len(parts) >= 4 and parts[0] == "projects" and parts[2] == "memory")
    expected_id = hashlib.sha256(relative.encode()).hexdigest()[:16]
    if (not allowed or ".." in parts or is_memory_index(path) or not is_under(path, CLAUDE_HOME)
            or incoming.get("id") != expected_id):
        return None
    return {"id": expected_id, "path": str(path), "kind": "memory", "scope":
            "user-memory" if parts[0] == "memory" else "project-memory", "mode": "fact-file",
            "frontmatter_scope": "common"}


def init(args: argparse.Namespace) -> None:
    if CONFIG.exists():
        raise SystemExit("Refusing to overwrite existing configuration")
    STATE.mkdir(parents=True, exist_ok=True)
    write_config({
        "machine": args.machine,
        "documents": native_documents(),
        "peers": [],
        # This section and environment.json are intentionally local-only.  They
        # are never put in an export bundle.
        "environment_discovery": {
            "tool_candidates": DEFAULT_TOOL_CANDIDATES,
            "history_files": [],
            "include_claude_history": True,
        },
    })
    log("initialized", machine=args.machine)
    scan_document_state(load_config())
    if not args.no_discover_environment:
        discover_environment(load_config())
    print(f"Created {CONFIG}; discovered {len(load_config()['documents'])} native Claude documents.")


def provision(args: argparse.Namespace) -> None:
    if CONFIG.exists():
        config = load_config()
    else:
        init(argparse.Namespace(machine=args.machine, no_discover_environment=args.no_discover_environment))
        config = load_config()
    refresh_documents(config)
    config = load_config()
    document_state = scan_document_state(config)
    record_machine_facts(config)
    record_fact_provenance(config, document_state)
    print(f"Provisioned {Path.cwd()} for {config['machine']}.")


def command_from_shell(line: str) -> str | None:
    """Extract only a command name; never retain shell arguments from history."""
    line = re.sub(r"^:\s*\d+:\d+;", "", line).strip()
    if not line or line.startswith("#"):
        return None
    try:
        words = shlex.split(line, comments=True)
    except ValueError:
        return None
    for word in words:
        if word in SHELL_OPERATORS:
            return None
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            continue
        if word in {"command", "builtin", "env", "sudo", "time"}:
            continue
        return Path(word).name
    return None


def shell_history_files(config: dict) -> list[Path]:
    settings = config.get("environment_discovery", {})
    configured = [Path(p).expanduser() for p in settings.get("history_files", []) if isinstance(p, str)]
    defaults = [Path.home() / ".zsh_history", Path.home() / ".bash_history"]
    # An explicit list is useful for a scoped audit or test; otherwise use the
    # normal local shell histories.
    return list(dict.fromkeys(configured if configured else defaults))


def claude_history_files(config: dict) -> list[Path]:
    if not config.get("environment_discovery", {}).get("include_claude_history", True):
        return []
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return []
    # JSONL transcript records are read locally and reduced immediately to
    # tool names; prompts, command arguments, and assistant text are discarded.
    return sorted(projects.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]


def commands_from_claude_record(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        # Transcript records are not schema-stable: nested metadata can use
        # an object for `name`. Only compare scalar command names.
        name = value.get("name")
        if isinstance(name, str) and name in {"Bash", "bash"}:
            command = value.get("input", {}).get("command") if isinstance(value.get("input"), dict) else None
            if isinstance(command, str):
                parsed = command_from_shell(command)
                if parsed:
                    found.append(parsed)
        for child in value.values():
            found.extend(commands_from_claude_record(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(commands_from_claude_record(child))
    return found


def discover_environment(config: dict) -> None:
    """Create a non-transferable, argument-free local machine profile."""
    settings = config.get("environment_discovery", {})
    candidates = settings.get("tool_candidates", DEFAULT_TOOL_CANDIDATES)
    candidates = [tool for tool in candidates if isinstance(tool, str) and re.match(r"^[A-Za-z0-9._+-]+$", tool)]
    used: Counter[str] = Counter()
    sources: list[dict[str, object]] = []
    for history in shell_history_files(config):
        if not history.is_file():
            continue
        count = 0
        try:
            for line in history.read_text(errors="replace").splitlines()[-10000:]:
                command = command_from_shell(line)
                if command:
                    used[command] += 1
                    count += 1
        except OSError as exc:
            sources.append({"kind": "shell_history", "read": False, "error": str(exc)})
            continue
        sources.append({"kind": "shell_history", "read": True, "commands_observed": count})
    claude_records = 0
    for transcript in claude_history_files(config):
        try:
            for line in transcript.read_text(errors="replace").splitlines():
                try:
                    used.update(commands_from_claude_record(json.loads(line)))
                    claude_records += 1
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    if claude_records:
        sources.append({"kind": "claude_history", "read": True, "records_observed": claude_records})
    observed = [name for name, _ in used.most_common(80)
                if re.match(r"^[A-Za-z0-9._+-]+$", name)]
    all_tools = list(dict.fromkeys(candidates + observed))
    profile = {
        "format": 1,
        "machine": config["machine"],
        "created": now(),
        "scope": "machine-local; never exported or merged",
        "sources": sources,
        "tools": [
            {"name": tool, "available": shutil.which(tool) is not None,
             "observed_uses": used[tool]}
            for tool in all_tools
        ],
    }
    STATE.mkdir(parents=True, exist_ok=True)
    if ENVIRONMENT.exists():
        # Environment data is local state, but preserve it with the same
        # non-destructive discipline as the synchronised documents.
        archive = STATE / "environment-snapshots" / f"{now()}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ENVIRONMENT, archive)
        log("environment_snapshot", snapshot=str(archive), reason="before environment rediscovery")
    ENVIRONMENT.write_text(json.dumps(profile, indent=2) + "\n")
    log("environment_discovered", tools=len(profile["tools"]), sources=len(sources))
    print(f"Wrote local environment profile: {ENVIRONMENT}")


def check(config: dict, kind: str | None = None) -> None:
    config = scoped_config(config, kind)
    document_state = scan_document_state(config)
    problems = []
    for doc in config["documents"]:
        path = doc_path(doc)
        if not path.exists():
            problems.append(f"{doc['id']}: missing {path}")
        else:
            problems += validate_document(doc, path.read_text(), doc["id"])
    problems += validate_provenance(config, document_state)
    if problems:
        print("CHECK FAILED", file=sys.stderr)
        print("\n".join(f"- {p}" for p in problems), file=sys.stderr)
        raise SystemExit(2)
    record_machine_facts(config)
    record_fact_provenance(config, document_state)
    print("CHECK OK: only explicit <common> fragments and scope: common fact files are eligible for export.")


def analysis_report(config: dict, kind: str | None = None) -> dict:
    """Return the current analysis gate without treating stale knowledge as synced."""
    config = scoped_config(config, kind)
    state = scan_document_state(config)
    changed = []
    missing = []
    for doc_id, value in state.get("documents", {}).items():
        if value.get("missing"):
            missing.append(doc_id)
        elif value.get("hash") != value.get("last_analyzed_hash"):
            changed.append(doc_id)
    return {"changed": changed, "missing": missing, "documents": state.get("documents", {})}


def require_analysis_current(config: dict, kind: str | None = None, endpoint: str = "local") -> None:
    report = analysis_report(config, kind)
    stale = report["changed"]
    missing = report["missing"]
    if stale or missing:
        details = []
        if stale:
            details.append("changed but unanalysed: " + ", ".join(stale))
        if missing:
            details.append("missing: " + ", ".join(missing))
        raise SystemExit("SYNC BLOCKED on %s: %s. Run /knowledge-sync:mine-facts there, "
                         "then rerun sync." % (endpoint, "; ".join(details)))


def exported_part_count(bundle_path: Path) -> int:
    """Count actual eligible payloads; configured local-only docs do not count."""
    try:
        bundle = json.loads(bundle_path.read_text())
        return sum(len(item.get("common", [])) for item in bundle.get("documents", [])
                   if isinstance(item, dict) and isinstance(item.get("common"), list))
    except (OSError, json.JSONDecodeError):
        raise SystemExit(f"Could not inspect generated bundle: {bundle_path}")


def export(config: dict, output: Path, kind: str | None = None) -> None:
    config = scoped_config(config, kind)
    check(config)
    docs = []
    for doc in config["documents"]:
        parts = shareable_parts(doc, doc_path(doc).read_text())
        docs.append({"id": doc["id"], "kind": doc.get("kind"), "mode": document_mode(doc),
                     "relative_path": str(doc_path(doc).relative_to(CLAUDE_HOME)), "common": parts,
                     "sha256": hashlib.sha256("\n\n".join(parts).encode()).hexdigest()})
    bundle = {"format": 1, "machine": config["machine"], "created": now(), "documents": docs}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, indent=2) + "\n")
    log("export", output=str(output), document_ids=[d["id"] for d in docs])
    record_sync_state(config, "export", document_ids=[d["id"] for d in docs])
    print(f"Wrote filtered bundle: {output}")


def run(command: list[str]) -> None:
    print("+ " + shell_join(command))
    subprocess.run(command, check=True)


def output(command: list[str]) -> str:
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def shell_join(parts: list[str]) -> str:
    """Render shell-safe command text on both Python 3.7 and newer runtimes."""
    join = getattr(shlex, "join", None)
    return join(parts) if join else " ".join(shlex.quote(part) for part in parts)


def remote_command(peer: dict, script: str, *arguments: str) -> list[str]:
    command = "python3 \"$HOME/%s\" %s" % (REMOTE_SCRIPT, shell_join(list(arguments)))
    # Do not rely on the account's login shell: it may be nushell, fish, etc.
    remote = "sh -lc " + shlex.quote(command)
    return ["ssh", peer["ssh_target"], remote]


def remote_shell(peer: dict, command: str) -> list[str]:
    """Run a POSIX-shell command without assuming the remote login shell."""
    return ["ssh", peer["ssh_target"], "sh -lc " + shlex.quote(command)]


def verify_peer(peer: dict) -> None:
    run(remote_shell(peer, "command -v python3 >/dev/null && command -v rsync >/dev/null && command -v claude >/dev/null"))


def remote_analyze(peer: dict, kind: str | None) -> None:
    """Ask Claude running on the peer to classify its own changed native docs."""
    kind_args = " --kind " + kind if kind else ""
    prompt = """Perform only the changed-document analysis for Knowledge Sync on this machine.
Run `python3 ~/.claude/knowledge-sync/bin/knowledge_sync.py analysis-status%s` and inspect every changed native document it reports. Do not recreate missing documents. For each new per-fact memory file, read `metadata.scope`: common is portable and any other label is local. For a new fact you derive, ask: Does this depend on tools, paths, credentials, services, configuration, or access that exist only on this one machine? A tool name alone is not machine-local. Use scope common when the answer is not clearly yes, but never put a secret in shared knowledge. For free-form steering use inline <common> only for portable content. Snapshot before any edit, never delete or replace facts, then run `check%s`, `mark-analyzed%s`, and `analysis-status%s`. Do not run sync; this caller performs transfer after both machines pass analysis.""" % (kind_args, kind_args, kind_args, kind_args)
    allowed_tools = "Read,Edit,Write,Bash(python3 ~/.claude/knowledge-sync/bin/knowledge_sync.py *)"
    command = ("cd \"$HOME\" && claude -p --no-session-persistence "
               "--permission-mode acceptEdits --permission-prompts none --allowedTools "
               + shlex.quote(allowed_tools) + " " + shlex.quote(prompt))
    run(remote_shell(peer, command))


def provision_peer(peer: dict, machine: str) -> None:
    """Install the helper under the peer's native ~/.claude directory."""
    run(remote_shell(peer, "mkdir -p \"$HOME/.claude/knowledge-sync/bin\""))
    for helper in ("knowledge_sync.py", "knowledge_sync_state.py"):
        run(["rsync", "-az", "-e", "ssh", str(Path(__file__).with_name(helper)),
             f"{peer['ssh_target']}:.claude/knowledge-sync/bin/{helper}"])
    run(remote_command(peer, "knowledge_sync.py", "provision", "--machine", machine,
                       "--no-discover-environment"))


def add_peer(config: dict, name: str, ssh_target: str) -> None:
    peer = {"name": name, "ssh_target": ssh_target}
    verify_peer(peer)
    peers = [item for item in config.get("peers", []) if item.get("name") != name]
    peers.append(peer)
    config["peers"] = peers
    write_config(config)
    log("peer_added", name=name, ssh_target=ssh_target)
    record_sync_state(config, "peer_added", name=name)
    print(f"Registered and verified peer {name}.")


def add_peer_command(config: dict, args: argparse.Namespace) -> None:
    add_peer(config, args.name, args.ssh_target)


def pair(args: argparse.Namespace) -> None:
    local_machine = args.machine or socket.gethostname().split(".")[0]
    if not CONFIG.exists():
        init(argparse.Namespace(machine=local_machine, no_discover_environment=False))
    config = load_config()
    refresh_documents(config)
    config = load_config()
    remote_name = args.name or output(["ssh", args.ssh_target, "hostname -s"])
    peer = {"name": remote_name, "ssh_target": args.ssh_target}
    verify_peer(peer)
    # Do not register either peer until the remote helper and documents exist.
    provision_peer(peer, remote_name)
    if args.register_reverse:
        if not args.local_ssh_target:
            raise SystemExit("--register-reverse requires --local-ssh-target")
        reverse_args = ["add-peer", "--name", config["machine"], "--ssh-target",
                        args.local_ssh_target]
        run(remote_command(peer, "knowledge_sync.py", *reverse_args))
    add_peer(config, remote_name, args.ssh_target)
    print(f"Paired {config['machine']} with {remote_name}.")


def sync(config: dict, peer_name: str, kind: str | None = None, pull: bool = False) -> None:
    peer = next((p for p in config.get("peers", []) if p["name"] == peer_name), None)
    if not peer:
        raise SystemExit(f"Unknown peer: {peer_name}")
    provision_peer(peer, peer_name)
    remote_analyze(peer, kind)
    remote_analysis_args = ["analysis-status"] + (["--kind", kind] if kind else [])
    try:
        remote_report = json.loads(output(remote_command(peer, "knowledge_sync.py", *remote_analysis_args)))
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise SystemExit("SYNC BLOCKED: could not verify remote analysis status: %s" % exc) from exc
    if remote_report.get("changed") or remote_report.get("missing"):
        details = []
        if remote_report.get("changed"):
            details.append("changed but unanalysed: " + ", ".join(remote_report["changed"]))
        if remote_report.get("missing"):
            details.append("missing: " + ", ".join(remote_report["missing"]))
        raise SystemExit("SYNC BLOCKED on %s: %s. Run /knowledge-sync:mine-facts on that machine, "
                         "then rerun sync." % (peer_name, "; ".join(details)))
    require_analysis_current(config, kind)
    config = scoped_config(config, kind)
    check(config)
    remote_check_args = ["check"] + (["--kind", kind] if kind else [])
    run(remote_command(peer, "knowledge_sync.py", *remote_check_args))
    stamp = now()
    remote_outbox = f".claude/knowledge-sync/outbound/{config['machine']}"
    remote_return = f"{remote_outbox}/{stamp}.json"
    inbound = STATE / "inbox" / peer_name / f"{stamp}.json"
    if pull:
        run(remote_shell(peer, "mkdir -p " + shlex.quote(remote_outbox)))
        remote_export_args = ["export", "--output", remote_return] + (["--kind", kind] if kind else [])
        run(remote_command(peer, "knowledge_sync.py", *remote_export_args))
        inbound.parent.mkdir(parents=True, exist_ok=True)
        run(["rsync", "-az", "-e", "ssh", f"{peer['ssh_target']}:{remote_return}", str(inbound)])
        if exported_part_count(inbound) == 0:
            raise SystemExit("SYNC FAILED LOUDLY: %s has no eligible shared knowledge after analysis; "
                             "review metadata.scope/common classifications." % peer_name)
        merge(config, inbound)
        log("sync_pull", peer=peer_name, inbound=str(inbound), kind=kind)
        record_sync_state(config, "pull", peer=peer_name, kind=kind, inbound=str(inbound))
        print(f"Pulled shared knowledge from {peer_name}.")
        return
    outbound = STATE / "outbound" / peer_name / f"{stamp}.json"
    export(config, outbound)
    if exported_part_count(outbound) == 0:
        raise SystemExit("SYNC FAILED LOUDLY: local analysis produced no eligible shared knowledge; "
                         "review metadata.scope/common classifications.")
    remote_inbox = f".claude/knowledge-sync/inbox/{config['machine']}"
    remote_bundle = f"{remote_inbox}/{stamp}.json"
    run(remote_shell(peer, "mkdir -p " + shlex.quote(remote_inbox)))
    run(["rsync", "-az", "-e", "ssh", str(outbound), f"{peer['ssh_target']}:{remote_bundle}"])
    remote_merge_args = ["merge", "--bundle", remote_bundle] + (["--kind", kind] if kind else [])
    run(remote_command(peer, "knowledge_sync.py", *remote_merge_args))
    run(remote_shell(peer, "mkdir -p " + shlex.quote(remote_outbox)))
    remote_export_args = ["export", "--output", remote_return] + (["--kind", kind] if kind else [])
    run(remote_command(peer, "knowledge_sync.py", *remote_export_args))
    inbound.parent.mkdir(parents=True, exist_ok=True)
    run(["rsync", "-az", "-e", "ssh", f"{peer['ssh_target']}:{remote_return}", str(inbound)])
    if exported_part_count(inbound) == 0:
        raise SystemExit("SYNC FAILED LOUDLY: %s has no eligible shared knowledge after analysis; "
                         "review metadata.scope/common classifications." % peer_name)
    merge(config, inbound)
    log("sync", peer=peer_name, outbound=str(outbound), inbound=str(inbound))
    record_sync_state(config, "sync", peer=peer_name, kind=kind, outbound=str(outbound), inbound=str(inbound))
    print(f"Two-way sync with {peer_name} completed.")


def merge(config: dict, bundle_path: Path, kind: str | None = None) -> None:
    try:
        bundle = json.loads(bundle_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Malformed bundle: {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != 1 or not isinstance(bundle.get("machine"), str):
        raise SystemExit("Unsupported or malformed bundle")
    if not isinstance(bundle.get("documents"), list):
        raise SystemExit("Malformed bundle documents")
    if bundle["machine"] == config["machine"]:
        raise SystemExit("Refusing to merge a bundle exported by this same machine")
    all_config = config
    config = scoped_config(config, kind)
    by_id = {doc["id"]: doc for doc in config["documents"]}
    changes = []
    fact_changes = []
    discovered = []
    received_ids = set()
    for incoming in bundle["documents"]:
        if not isinstance(incoming, dict) or not isinstance(incoming.get("id"), str) or not isinstance(incoming.get("common"), list):
            raise SystemExit("Malformed bundle document")
        if incoming["id"] in received_ids:
            raise SystemExit(f"Malformed bundle: duplicate document id {incoming['id']}")
        received_ids.add(incoming["id"])
        expected_hash = hashlib.sha256("\n\n".join(incoming["common"]).encode()).hexdigest() if all(isinstance(x, str) for x in incoming["common"]) else None
        if incoming.get("sha256") != expected_hash:
            raise SystemExit(f"Malformed bundle: invalid hash for {incoming['id']}")
        if kind is not None and incoming.get("kind") != kind:
            continue
        mode = incoming.get("mode", "free-form")
        if mode not in {"free-form", "fact-file"}:
            raise SystemExit(f"Malformed bundle: unsupported document mode for {incoming['id']}")
        if mode == "fact-file" and incoming.get("kind") != "memory":
            raise SystemExit(f"Malformed fact-file kind for {incoming['id']}")
        doc = by_id.get(incoming.get("id"))
        if not doc and mode == "fact-file" and incoming["common"]:
            doc = incoming_fact_document(incoming)
            if not doc:
                raise SystemExit(f"Malformed or unsafe fact-file path for {incoming['id']}")
            by_id[doc["id"]] = doc
            discovered.append(doc)
        if not doc:
            continue
        path = doc_path(doc)
        if mode == "fact-file":
            if len(incoming["common"]) > 1:
                raise SystemExit(f"Malformed fact-file bundle for {doc['id']}")
            if not incoming["common"]:
                continue
            remote_text = incoming["common"][0]
            if frontmatter_scope(remote_text) != "common" or SECRET.search(remote_text):
                raise SystemExit(f"Merge refused: remote {doc['id']} is not a safe scope: common fact file")
            if not path.exists():
                fact_changes.append((path, remote_text if remote_text.endswith("\n") else remote_text + "\n",
                                     doc["id"], "created shared fact file"))
                continue
            existing = path.read_text()
            if frontmatter_scope(existing) != "common":
                log("merge_fact_file_skipped_local_scope", source=bundle["machine"], document=doc["id"])
                continue
            if canonical(existing) != canonical(remote_text):
                fact_changes.append((path, "\n\n" + conflict(path.stem, existing, remote_text, bundle["machine"]) + "\n",
                                     doc["id"], "preserved fact-file conflict"))
            continue
        if not path.exists():
            continue
        existing = path.read_text()
        problems = validate_document(doc, existing, doc["id"])
        for part in incoming["common"]:
            problems += validate_remote_part(part, f"remote {doc['id']}")
        if problems:
            raise SystemExit("Merge refused:\n" + "\n".join(problems))
        local_parts = common_parts(existing)
        local_facts = {name: body for part in local_parts for name, body in FACT.findall(part)}
        additions = []
        remote_facts_seen = {}
        for part in incoming["common"]:
            remote_facts = FACT.findall(part)
            if remote_facts:
                for name, body in remote_facts:
                    prior = remote_facts_seen.get(name)
                    if prior is not None and canonical(prior) != canonical(body):
                        additions.append(conflict(name, prior, body, bundle["machine"]))
                    elif name not in local_facts:
                        additions.append(f'<!-- knowledge-sync:fact name="{name}" -->\n{body}\n<!-- /knowledge-sync:fact -->')
                    elif canonical(local_facts[name]) != canonical(body):
                        additions.append(conflict(name, local_facts[name], body, bundle["machine"]))
                    remote_facts_seen[name] = body
                continue
            if canonical(part) not in {canonical(p) for p in local_parts}:
                additions.append(part.strip())
        unique = []
        for item in additions:
            if canonical(item) not in {canonical(x) for x in unique}:
                unique.append(item)
        if unique:
            changes.append((path, "\n\n<common>\n" + "\n\n".join(unique) + "\n</common>\n", doc["id"], len(unique)))
    if not changes and not fact_changes:
        log("merge_noop", source=bundle["machine"], bundle=str(bundle_path))
        print("No new shared knowledge to merge.")
        return
    snapshot_config = {**config, "documents": config["documents"] + discovered}
    snapshot_id = snapshot(snapshot_config, f"before merge from {bundle['machine']}")
    for path, addition, doc_id, count in changes:
        with path.open("a") as stream:
            stream.write(addition)
        log("merge", source=bundle["machine"], document=doc_id, additions=count, snapshot=snapshot_id)
    for path, addition, doc_id, action in fact_changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if path.exists() else "w") as stream:
            stream.write(addition)
        log("merge_fact_file", source=bundle["machine"], document=doc_id, action=action, snapshot=snapshot_id)
    if discovered:
        all_config["documents"].extend(discovered)
        write_config(all_config)
    record_sync_state(config, "merge", source=bundle["machine"], kind=kind, snapshot=snapshot_id)
    print(f"Merged shared knowledge from {bundle['machine']} after snapshot {snapshot_id}.")


def status(config: dict) -> None:
    snapshots = sorted((STATE / "snapshots").glob("*/manifest.json")) if (STATE / "snapshots").exists() else []
    print(f"machine: {config['machine']}")
    print("documents:")
    for doc in config["documents"]:
        print(f"  - {doc['id']}: {doc['path']}")
    print("snapshots:")
    for manifest in snapshots:
        data = json.loads(manifest.read_text())
        print(f"  - {data['id']} ({data['reason']})")
    machine_facts = read_json(MACHINE_FACTS, {"facts": {}}).get("facts", {})
    sync_state = read_json(SYNC_STATE, {"events": []}).get("events", [])
    document_state = read_json(DOCUMENT_STATE, {"documents": {}}).get("documents", {})
    missing = [key for key, value in document_state.items() if value.get("missing")]
    print(f"machine-specific facts: {sum(v.get('active', False) for v in machine_facts.values())}")
    print(f"sync metadata events: {len(sync_state)}")
    print("missing documents: " + (", ".join(missing) if missing else "none"))


def analysis_status(config: dict, kind: str | None = None) -> None:
    report = analysis_report(config, kind)
    # Keep the legacy key for callers that have not yet upgraded.
    print(json.dumps({**report, "changed_or_previously_changed": report["changed"]}, indent=2))


def mark_analyzed(config: dict, kind: str | None = None) -> None:
    config = scoped_config(config, kind)
    state = scan_document_state(config)
    for value in state.get("documents", {}).values():
        if not value.get("missing") and value.get("hash"):
            value["last_analyzed_hash"] = value["hash"]
            value["last_analyzed_at"] = now()
    DOCUMENT_STATE.write_text(json.dumps(state, indent=2) + "\n")
    record_fact_provenance(config, state)
    log("facts_analyzed")
    print("Recorded current document hashes as analyzed.")


def rollback(config: dict, snapshot_id: str) -> None:
    folder = STATE / "snapshots" / snapshot_id
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Unknown snapshot: {snapshot_id}")
    manifest = json.loads(manifest_path.read_text())
    before = snapshot(config, f"before rollback to {snapshot_id}")
    for item in manifest["documents"]:
        target = Path(item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / item["saved_as"], target)
    log("rollback", restored=snapshot_id, snapshot_before=before)
    print(f"Restored {snapshot_id}; current state was snapshotted as {before}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init"); p.add_argument("--machine", required=True); p.add_argument("--no-discover-environment", action="store_true")
    p = sub.add_parser("provision"); p.add_argument("--machine", required=True); p.add_argument("--no-discover-environment", action="store_true")
    p = sub.add_parser("add-peer"); p.add_argument("--name", required=True); p.add_argument("--ssh-target", required=True)
    p = sub.add_parser("pair"); p.add_argument("--ssh-target", required=True); p.add_argument("--name"); p.add_argument("--machine"); p.add_argument("--register-reverse", action="store_true"); p.add_argument("--local-ssh-target")
    sub.add_parser("discover-environment")
    sub.add_parser("record-machine-facts")
    p = sub.add_parser("analysis-status"); p.add_argument("--kind", choices=("memory", "steering"))
    p = sub.add_parser("mark-analyzed"); p.add_argument("--kind", choices=("memory", "steering"))
    p = sub.add_parser("check"); p.add_argument("--kind", choices=("memory", "steering"))
    p = sub.add_parser("snapshot"); p.add_argument("--reason", default="manual snapshot")
    p = sub.add_parser("export"); p.add_argument("--output", type=Path, required=True); p.add_argument("--kind", choices=("memory", "steering"))
    p = sub.add_parser("merge"); p.add_argument("--bundle", type=Path, required=True); p.add_argument("--kind", choices=("memory", "steering"))
    p = sub.add_parser("sync"); p.add_argument("--peer", required=True); p.add_argument("--kind", choices=("memory", "steering")); p.add_argument("--pull", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("rollback"); p.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    if args.command == "init": return init(args)
    if args.command == "provision": return provision(args)
    if args.command == "pair": return pair(args)
    config = load_config()
    refresh_documents(config)
    config = load_config()
    if args.command == "add-peer": return add_peer_command(config, args)
    if args.command == "discover-environment": return discover_environment(config)
    if args.command == "record-machine-facts": return record_machine_facts(config)
    if args.command == "analysis-status": return analysis_status(config, args.kind)
    if args.command == "mark-analyzed": return mark_analyzed(config, args.kind)
    if args.command == "check": return check(config, args.kind)
    if args.command == "snapshot": print(snapshot(config, args.reason)); return
    if args.command == "export": return export(config, args.output, args.kind)
    if args.command == "merge": return merge(config, args.bundle, args.kind)
    if args.command == "sync": return sync(config, args.peer, args.kind, args.pull)
    if args.command == "status": return status(config)
    return rollback(config, args.snapshot)


if __name__ == "__main__":
    main()
