"""Offline paired backup of durable state and ledger databases."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from .core import Config, ConflictError, IntegrityError, NotFoundError, ValidationError, json_text, utcnow
from .storage import RuntimeLease


FILES = ("knowledge.state.db", "knowledge.ledger.db")


def authored_fingerprint(config: Config):
    entries = []
    for directory in ("control", "knowledge", "connectors"):
        for path in sorted((config.root / directory).rglob("*")):
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(config.root).parts):
                entries.append([str(path.relative_to(config.root)), hashlib.sha256(path.read_bytes()).hexdigest()])
    return hashlib.sha256(json_text(entries).encode()).hexdigest()


def backup(config: Config, destination):
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValidationError("backup destination already exists")
    lease = RuntimeLease(config, exclusive=True)
    try:
        for name in FILES:
            if not (config.runtime / name).is_file():
                raise NotFoundError(f"missing durable file {name}; initialize service first")
        destination.mkdir(parents=True)
        hashes = {}
        for name in FILES:
            with sqlite3.connect(str(config.runtime / name)) as source, sqlite3.connect(str(destination / name)) as target:
                source.backup(target)
            hashes[name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
    finally:
        lease.close()
    manifest = {"format": "knowledgeos.backup.v1", "files": hashes,
                "authored_fingerprint": authored_fingerprint(config), "created_at": utcnow()}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def restore(config: Config, source):
    source = Path(source).expanduser().resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("format") != "knowledgeos.backup.v1":
        raise ValidationError("unsupported backup format")
    if manifest.get("authored_fingerprint") != authored_fingerprint(config):
        raise ConflictError("restore requires the same authored knowledge/control checkout")
    for name in FILES:
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != manifest["files"][name]:
            raise IntegrityError(f"backup checksum mismatch: {name}")
    lease = RuntimeLease(config, exclusive=True)
    try:
        if list(config.runtime.glob("knowledge.*.db*")):
            raise ConflictError("restore requires empty runtime")
        for name in FILES:
            shutil.copy2(source / name, config.runtime / name)
    finally:
        lease.close()
    return {"restored": list(FILES), "next_step": "rebuild the disposable index, then verify-ledger"}
