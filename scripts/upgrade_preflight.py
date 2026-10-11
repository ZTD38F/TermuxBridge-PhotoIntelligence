#!/data/data/com.termux/files/usr/bin/python3
"""Read-only, fail-closed PhotoIntelligence upgrade compatibility preflight.
Author: @dvkrum

No imports from the target runtime, network, writes, service restarts or cleanup.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3

VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def constant(source: Path, key: str):
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == key:
                    if isinstance(stmt.value, ast.Constant):
                        return stmt.value.value
    raise ValueError(f"missing_constant_{key}")


def semver(value: str):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError("invalid_version")
    return tuple(map(int, value.split(".")))


def manifest(root: Path):
    data = json.loads((root / "addon.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("name") != "photo-intelligence":
        raise ValueError("invalid_addon_identity")
    return data


def check(*, candidate: Path, installed: Path, database: Path,
          bridge_runtime: Path, minimum_free_mb: int = 512):
    issues = []
    facts = {"read_only": True, "photos_modified": False}
    if candidate.resolve() == installed.resolve():
        issues.append("CANDIDATE_IS_INSTALLED")
    if not all((candidate / f).is_file() for f in
               ("addon.json", "service.py", "adapter.py",
                "runtime/photo_config.py", "runtime/db_migrations.py",
                "photo-intelligence")):
        issues.append("CANDIDATE_INCOMPLETE")
    try:
        target = manifest(candidate)
        current = manifest(installed)
        facts["installed_version"] = current["version"]
        facts["candidate_version"] = target["version"]
        if semver(target["version"]) <= semver(current["version"]):
            issues.append("CANDIDATE_NOT_NEWER")
        if target.get("author") != "@dvkrum":
            issues.append("AUTHOR_MISMATCH")
        if target.get("abi") != current.get("abi"):
            issues.append("ABI_MISMATCH")
        if constant(candidate / "runtime/photo_config.py", "ADDON_VERSION") != target["version"]:
            issues.append("VERSION_MISMATCH")
        candidate_schema = constant(candidate / "runtime/db_migrations.py", "TARGET_VERSION")
        if not isinstance(candidate_schema, int):
            raise ValueError("invalid_schema_target")
        facts["candidate_schema"] = candidate_schema
        bridge_version = constant(bridge_runtime / "bridge_server.py", "BRIDGE_VERSION")
        facts["bridge_version"] = bridge_version
        if semver(bridge_version) < semver(target["bridge_min_version"]):
            issues.append("BRIDGE_VERSION_TOO_OLD")
    except (OSError, ValueError, KeyError, SyntaxError, TypeError,
            json.JSONDecodeError) as exc:
        issues.append("INVALID_RELEASE_METADATA")
        facts["metadata_error_type"] = type(exc).__name__
        candidate_schema = None
    if not database.is_file():
        issues.append("DATABASE_UNAVAILABLE")
    else:
        try:
            # Connect using SQLite URI read-only mode, never journal_mode=WAL.
            uri = database.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=3)) as conn:
                integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
                schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
            facts["db_integrity"] = integrity
            facts["installed_schema"] = schema
            if integrity != "ok":
                issues.append("DATABASE_INTEGRITY_FAILURE")
            if candidate_schema is not None and schema > candidate_schema:
                issues.append("UNSUPPORTED_SCHEMA_DOWNGRADE")
            elif candidate_schema is not None and schema < candidate_schema:
                issues.append("MIGRATION_REQUIRED_STAGED_TEST")
        except (OSError, ValueError, sqlite3.Error) as exc:
            issues.append("DATABASE_READ_FAILED")
            facts["database_error_type"] = type(exc).__name__
    try:
        free = shutil.disk_usage(installed).free // (1024 * 1024)
        facts["free_storage_mb"] = free
        if free < minimum_free_mb:
            issues.append("INSUFFICIENT_STORAGE")
    except OSError:
        issues.append("STORAGE_CHECK_FAILED")
    return {"ok": not issues, "issues": sorted(set(issues)), **facts}


def main():
    home = Path.home()
    default_addon = home / ".termux-mcp-bridge/addons/photo-intelligence"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--installed", type=Path, default=default_addon / "current")
    p.add_argument("--database", type=Path, default=default_addon / "state/gallery.sqlite3")
    p.add_argument("--bridge-runtime", type=Path, default=home / "termux-mcp-bridge/current")
    p.add_argument("--minimum-free-mb", type=int, default=512)
    args = p.parse_args()
    report = check(candidate=args.candidate, installed=args.installed,
                   database=args.database, bridge_runtime=args.bridge_runtime,
                   minimum_free_mb=max(0, args.minimum_free_mb))
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
