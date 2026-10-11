"""Versioned SQLite migrations with bounded automatic backups.
Author: @dvkrum
"""
from __future__ import annotations
import os,sqlite3,time
from pathlib import Path
from photo_config import STATE_DIR
TARGET_VERSION=1

def _backup(path:Path,current:int,target:int)->Path:
    outdir=STATE_DIR/"backups";outdir.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=int(time.time());dst=outdir/f"gallery-schema-v{current}-to-v{target}-{stamp}.sqlite3"
    with sqlite3.connect(path) as src, sqlite3.connect(dst) as out:src.backup(out)
    dst.chmod(0o600)
    backups=sorted(outdir.glob("gallery-schema-*.sqlite3"),key=lambda p:p.stat().st_mtime,reverse=True)
    for old in backups[3:]:
        try:old.unlink()
        except OSError:pass
    return dst

def migrate(path:Path)->dict:
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    existed=path.exists() and path.stat().st_size>0
    with sqlite3.connect(path) as c:
        current=int(c.execute("PRAGMA user_version").fetchone()[0])
    backup=None
    if existed and current<TARGET_VERSION:backup=_backup(path,current,TARGET_VERSION)
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        if current<1:
            c.execute("""CREATE TABLE IF NOT EXISTS photo_schema_migrations(
                version INTEGER PRIMARY KEY, applied_at REAL NOT NULL, note TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS pipeline_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
                stage TEXT NOT NULL, state TEXT NOT NULL, duration_ms INTEGER,
                item_count INTEGER, detail TEXT)""")
            c.execute("CREATE INDEX IF NOT EXISTS pipeline_events_created_idx ON pipeline_events(created_at DESC)")
            c.execute("INSERT OR REPLACE INTO photo_schema_migrations(version,applied_at,note) VALUES(1,?,?)",(time.time(),"pipeline foundation"))
            c.execute("PRAGMA user_version=1")
        c.commit()
        integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
    return {"ok":integrity=="ok","from":current,"to":TARGET_VERSION,"backup":str(backup) if backup else None,"integrity":integrity}
