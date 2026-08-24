#!/usr/bin/env python3
"""Additive, local-only support for the knowledge-sync Claude Code skill."""
from __future__ import annotations

import argparse
import datetime as dt
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

STATE = Path(".knowledge-sync")
CONFIG = STATE / "config.json"
LOG = STATE / "log.jsonl"
ENVIRONMENT = STATE / "environment.json"
MACHINE_FACTS = STATE / "machine-facts.json"
SYNC_STATE = STATE / "sync-state.json"
REMOTE_SCRIPT = ".knowledge-sync/bin/knowledge_sync.py"
DEFAULT_REMOTE_WORKSPACE = ".claude-knowledge-sync"
FACT = re.compile(r'<!-- knowledge-sync:fact name="([^"]+)" -->\s*(.*?)\s*<!-- /knowledge-sync:fact -->', re.S)
COMMON = re.compile(r"<common>\s*(.*?)\s*</common>", re.S)
SCOPE = re.compile(r"<([A-Za-z][A-Za-z0-9_-]*)>\s*(.*?)\s*</\1>", re.S)
SECRET = re.compile(r"(?:AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_-]?key|secret|token|password)\s*[:=]\s*[^\s]{8,}|gh[pousr]_[A-Za-z0-9_]{20,})", re.I)
DEFAULT_TOOL_CANDIDATES = [
    "claude", "codex", "git", "rg", "python3", "node", "npm", "bun", "go",
    "docker", "kubectl", "helm", "terraform", "ansible", "ssh", "rsync",
]
SHELL_OPERATORS = {"|", "||", "&&", ";", "&", "("}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit("No .knowledge-sync/config.json. Run init first.")
    try:
        config = json.loads(CONFIG.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid config: {exc}") from exc
    if not isinstance(config.get("machine"), str) or not config["machine"]:
        raise SystemExit("config.machine must be a non-empty string")
    docs = config.get("documents")
    if not isinstance(docs, list) or not docs:
        raise SystemExit("config.documents must be a non-empty list")
    for doc in docs:
        path = Path(doc.get("path", ""))
        if not isinstance(doc.get("id"), str) or path.is_absolute() or ".." in path.parts:
            raise SystemExit("Every document needs an id and a workspace-relative path without '..'")
    for peer in config.get("peers", []):
        if not all(isinstance(peer.get(key), str) and peer[key] for key in ("name", "ssh_target")):
            raise SystemExit("Every peer needs non-empty name and ssh_target fields")
        peer.setdefault("workspace", DEFAULT_REMOTE_WORKSPACE)
    return config


def log(event: str, **data: object) -> None:
    STATE.mkdir(exist_ok=True)
    with LOG.open("a") as stream:
        stream.write(json.dumps({"time": now(), "event": event, **data}, sort_keys=True) + "\n")


def doc_path(doc: dict) -> Path:
    return Path(doc["path"])


def ensure_documents(config: dict) -> None:
    """Create safe empty templates for missing configured documents only."""
    for doc in config["documents"]:
        path = doc_path(doc)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# {doc['kind'].title()}\n\n"
            f"<{config['machine']}>\n"
            "<!-- Add machine-specific facts here with knowledge-sync:fact markers. -->\n"
            f"</{config['machine']}>\n\n"
            "<common>\n"
            "<!-- Add portable facts here with knowledge-sync:fact markers. -->\n"
            "</common>\n"
        )
        log("document_scaffolded", document=doc["id"], path=str(path))


def write_config(config: dict) -> None:
    CONFIG.write_text(json.dumps(config, indent=2) + "\n")


def scoped_config(config: dict, kind: str | None) -> dict:
    if kind is None:
        return config
    if kind not in {"memory", "steering"}:
        raise SystemExit("--kind must be memory or steering")
    selected = [doc for doc in config["documents"] if doc.get("kind") == kind]
    if not selected:
        raise SystemExit(f"No configured document has kind: {kind}")
    return {**config, "documents": selected}


def read_json(path: Path, fallback: dict) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else fallback
    except json.JSONDecodeError:
        return fallback


def record_machine_facts(config: dict) -> None:
    """Record explicitly tagged non-common facts locally; never remove history."""
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
        for scope, content in SCOPE.findall(path.read_text()):
            if scope == "common":
                continue
            for name, body in FACT.findall(content):
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


def init(args: argparse.Namespace) -> None:
    if CONFIG.exists():
        raise SystemExit("Refusing to overwrite existing configuration")
    STATE.mkdir(exist_ok=True)
    write_config({
        "machine": args.machine,
        "documents": [
            {"id": "memory", "path": ".claude/memory.md", "kind": "memory"},
            {"id": "steering", "path": "CLAUDE.md", "kind": "steering"}
        ],
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
    ensure_documents(load_config())
    if not args.no_discover_environment:
        discover_environment(load_config())
    print(f"Created {CONFIG}; edit document paths and peers before syncing.")


def provision(args: argparse.Namespace) -> None:
    if CONFIG.exists():
        config = load_config()
    else:
        init(argparse.Namespace(machine=args.machine, no_discover_environment=args.no_discover_environment))
        config = load_config()
    ensure_documents(config)
    record_machine_facts(config)
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
    STATE.mkdir(exist_ok=True)
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
    problems = []
    for doc in scoped_config(config, kind)["documents"]:
        path = doc_path(doc)
        if not path.exists():
            problems.append(f"{doc['id']}: missing {path}")
        else:
            problems += validate_text(path.read_text(), doc["id"])
    if problems:
        print("CHECK FAILED", file=sys.stderr)
        print("\n".join(f"- {p}" for p in problems), file=sys.stderr)
        raise SystemExit(2)
    record_machine_facts(scoped_config(config, kind))
    print("CHECK OK: only explicit <common> fragments are eligible for export.")


def export(config: dict, output: Path, kind: str | None = None) -> None:
    config = scoped_config(config, kind)
    check(config)
    docs = []
    for doc in config["documents"]:
        parts = common_parts(doc_path(doc).read_text())
        docs.append({"id": doc["id"], "kind": doc.get("kind"), "common": parts,
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
    """shlex.join is only available starting with Python 3.8."""
    join = getattr(shlex, "join", None)
    return join(parts) if join else " ".join(shlex.quote(part) for part in parts)


def remote_command(peer: dict, script: str, *arguments: str) -> list[str]:
    command = "cd %s && python3 %s %s" % (
        shlex.quote(peer["workspace"]),
        shlex.quote(REMOTE_SCRIPT),
        shell_join(list(arguments)),
    )
    # Do not rely on the account's login shell: it may be nushell, fish, etc.
    remote = "sh -lc " + shlex.quote(command)
    return ["ssh", peer["ssh_target"], remote]


def remote_shell(peer: dict, command: str) -> list[str]:
    """Run a POSIX-shell command without assuming the remote login shell."""
    return ["ssh", peer["ssh_target"], "sh -lc " + shlex.quote(command)]


def verify_peer(peer: dict) -> None:
    run(remote_shell(peer, "command -v python3 >/dev/null && command -v rsync >/dev/null"))


def provision_peer(peer: dict, machine: str) -> None:
    """Install only this script and scaffold its isolated remote workspace."""
    workspace = peer.get("workspace", DEFAULT_REMOTE_WORKSPACE)
    run(remote_shell(peer, "mkdir -p " + shlex.quote(f"{workspace}/.knowledge-sync/bin")))
    run(["rsync", "-az", "-e", "ssh", str(Path(__file__).resolve()),
         f"{peer['ssh_target']}:{workspace}/{REMOTE_SCRIPT}"])
    run(remote_command(peer, "knowledge_sync.py", "provision", "--machine", machine,
                       "--no-discover-environment"))


def add_peer(config: dict, name: str, ssh_target: str, workspace: str | None) -> None:
    peer = {"name": name, "ssh_target": ssh_target,
            "workspace": workspace or DEFAULT_REMOTE_WORKSPACE}
    verify_peer(peer)
    peers = [item for item in config.get("peers", []) if item.get("name") != name]
    peers.append(peer)
    config["peers"] = peers
    write_config(config)
    log("peer_added", name=name, ssh_target=ssh_target, workspace=peer["workspace"])
    record_sync_state(config, "peer_added", name=name)
    print(f"Registered and verified peer {name}.")


def add_peer_command(config: dict, args: argparse.Namespace) -> None:
    add_peer(config, args.name, args.ssh_target, args.workspace)


def pair(args: argparse.Namespace) -> None:
    local_machine = args.machine or socket.gethostname().split(".")[0]
    if not CONFIG.exists():
        init(argparse.Namespace(machine=local_machine, no_discover_environment=False))
    config = load_config()
    remote_workspace = args.workspace or DEFAULT_REMOTE_WORKSPACE
    remote_name = args.name or output(["ssh", args.ssh_target, "hostname -s"])
    peer = {"name": remote_name, "ssh_target": args.ssh_target, "workspace": remote_workspace}
    verify_peer(peer)
    # Do not register either peer until the remote helper and documents exist.
    provision_peer(peer, remote_name)
    if args.register_reverse:
        if not args.local_ssh_target:
            raise SystemExit("--register-reverse requires --local-ssh-target")
        reverse_args = ["add-peer", "--name", config["machine"], "--ssh-target",
                        args.local_ssh_target, "--workspace", str(Path.cwd().resolve())]
        run(remote_command(peer, "knowledge_sync.py", *reverse_args))
    add_peer(config, remote_name, args.ssh_target, remote_workspace)
    print(f"Paired {config['machine']} with {remote_name}.")


def sync(config: dict, peer_name: str, kind: str | None = None, pull: bool = False) -> None:
    peer = next((p for p in config.get("peers", []) if p["name"] == peer_name), None)
    if not peer:
        raise SystemExit(f"Unknown peer: {peer_name}")
    config = scoped_config(config, kind)
    check(config)
    provision_peer(peer, peer_name)
    remote_check_args = ["check"] + (["--kind", kind] if kind else [])
    run(remote_command(peer, "knowledge_sync.py", *remote_check_args))
    stamp = now()
    remote_outbox = f"{peer['workspace']}/.knowledge-sync/outbound/{config['machine']}"
    remote_return = f"{remote_outbox}/{stamp}.json"
    inbound = STATE / "inbox" / peer_name / f"{stamp}.json"
    if pull:
        run(remote_shell(peer, "mkdir -p " + shlex.quote(remote_outbox)))
        remote_export_args = ["export", "--output", remote_return] + (["--kind", kind] if kind else [])
        run(remote_command(peer, "knowledge_sync.py", *remote_export_args))
        inbound.parent.mkdir(parents=True, exist_ok=True)
        run(["rsync", "-az", "-e", "ssh", f"{peer['ssh_target']}:{remote_return}", str(inbound)])
        merge(config, inbound)
        log("sync_pull", peer=peer_name, inbound=str(inbound), kind=kind)
        record_sync_state(config, "pull", peer=peer_name, kind=kind, inbound=str(inbound))
        print(f"Pulled shared knowledge from {peer_name}.")
        return
    outbound = STATE / "outbound" / peer_name / f"{stamp}.json"
    export(config, outbound)
    remote_inbox = f"{peer['workspace']}/.knowledge-sync/inbox/{config['machine']}"
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
    merge(config, inbound)
    log("sync", peer=peer_name, outbound=str(outbound), inbound=str(inbound))
    record_sync_state(config, "sync", peer=peer_name, kind=kind, outbound=str(outbound), inbound=str(inbound))
    print(f"Two-way sync with {peer_name} completed.")


def merge(config: dict, bundle_path: Path, kind: str | None = None) -> None:
    bundle = json.loads(bundle_path.read_text())
    if bundle.get("format") != 1 or not isinstance(bundle.get("machine"), str):
        raise SystemExit("Unsupported or malformed bundle")
    if bundle["machine"] == config["machine"]:
        raise SystemExit("Refusing to merge a bundle exported by this same machine")
    config = scoped_config(config, kind)
    by_id = {doc["id"]: doc for doc in config["documents"]}
    changes = []
    for incoming in bundle.get("documents", []):
        doc = by_id.get(incoming.get("id"))
        if not doc or not isinstance(incoming.get("common"), list):
            continue
        path = doc_path(doc)
        if not path.exists():
            continue
        existing = path.read_text()
        problems = validate_text(existing, doc["id"])
        for part in incoming["common"]:
            problems += validate_text(f"<common>\n{part}\n</common>", f"remote {doc['id']}")
        if problems:
            raise SystemExit("Merge refused:\n" + "\n".join(problems))
        local_parts = common_parts(existing)
        local_facts = {name: body for part in local_parts for name, body in FACT.findall(part)}
        additions = []
        for part in incoming["common"]:
            remote_facts = FACT.findall(part)
            if remote_facts:
                for name, body in remote_facts:
                    if name not in local_facts:
                        additions.append(part.strip())
                    elif canonical(local_facts[name]) != canonical(body):
                        additions.append(conflict(name, local_facts[name], body, bundle["machine"]))
                continue
            if canonical(part) not in {canonical(p) for p in local_parts}:
                additions.append(part.strip())
        unique = []
        for item in additions:
            if canonical(item) not in {canonical(x) for x in unique}:
                unique.append(item)
        if unique:
            changes.append((path, "\n\n<common>\n" + "\n\n".join(unique) + "\n</common>\n", doc["id"], len(unique)))
    if not changes:
        log("merge_noop", source=bundle["machine"], bundle=str(bundle_path))
        print("No new shared knowledge to merge.")
        return
    snapshot_id = snapshot(config, f"before merge from {bundle['machine']}")
    for path, addition, doc_id, count in changes:
        with path.open("a") as stream:
            stream.write(addition)
        log("merge", source=bundle["machine"], document=doc_id, additions=count, snapshot=snapshot_id)
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
    print(f"machine-specific facts: {sum(v.get('active', False) for v in machine_facts.values())}")
    print(f"sync metadata events: {len(sync_state)}")


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
    p = sub.add_parser("add-peer"); p.add_argument("--name", required=True); p.add_argument("--ssh-target", required=True); p.add_argument("--workspace")
    p = sub.add_parser("pair"); p.add_argument("--ssh-target", required=True); p.add_argument("--workspace"); p.add_argument("--name"); p.add_argument("--machine"); p.add_argument("--register-reverse", action="store_true"); p.add_argument("--local-ssh-target")
    sub.add_parser("discover-environment")
    sub.add_parser("record-machine-facts")
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
    if args.command == "add-peer": return add_peer_command(config, args)
    if args.command == "discover-environment": return discover_environment(config)
    if args.command == "record-machine-facts": return record_machine_facts(config)
    if args.command == "check": return check(config, args.kind)
    if args.command == "snapshot": print(snapshot(config, args.reason)); return
    if args.command == "export": return export(config, args.output, args.kind)
    if args.command == "merge": return merge(config, args.bundle, args.kind)
    if args.command == "sync": return sync(config, args.peer, args.kind, args.pull)
    if args.command == "status": return status(config)
    return rollback(config, args.snapshot)


if __name__ == "__main__":
    main()
