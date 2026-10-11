"""Versioned SQLite migrations with bounded automatic backups.
Author: @dvkrum
"""
from __future__ import annotations
import os,sqlite3,time
from pathlib import Path
from photo_config import STATE_DIR
TARGET_VERSION=3

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
        c.execute("""CREATE TABLE IF NOT EXISTS photo_schema_migrations(
            version INTEGER PRIMARY KEY, applied_at REAL NOT NULL, note TEXT NOT NULL)""")
        if current<1:
            c.execute("""CREATE TABLE IF NOT EXISTS pipeline_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
                stage TEXT NOT NULL, state TEXT NOT NULL, duration_ms INTEGER,
                item_count INTEGER, detail TEXT)""")
            c.execute("CREATE INDEX IF NOT EXISTS pipeline_events_created_idx ON pipeline_events(created_at DESC)")
            c.execute("INSERT OR REPLACE INTO photo_schema_migrations(version,applied_at,note) VALUES(1,?,?)",(time.time(),"pipeline foundation"))
            c.execute("PRAGMA user_version=1")
        if current<2:
            c.execute("CREATE TABLE IF NOT EXISTS gallery_cleanup_plans(plan_id TEXT PRIMARY KEY,created_at REAL NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL)")
            c.execute("""CREATE TABLE IF NOT EXISTS gallery_cleanup_actions(
              action_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,image_id INTEGER NOT NULL,
              original_path TEXT NOT NULL,quarantine_path TEXT NOT NULL,size INTEGER NOT NULL,mtime REAL NOT NULL,
              state TEXT NOT NULL,quarantined_at REAL NOT NULL,restored_at REAL,purged_at REAL,
              FOREIGN KEY(plan_id) REFERENCES gallery_cleanup_plans(plan_id) ON DELETE CASCADE)""")
            c.execute("CREATE INDEX IF NOT EXISTS cleanup_actions_plan_idx ON gallery_cleanup_actions(plan_id,state)")
            c.execute("INSERT OR REPLACE INTO photo_schema_migrations(version,applied_at,note) VALUES(2,?,?)",(time.time(),"reversible cleanup quarantine"))
            c.execute("PRAGMA user_version=2")
        if current<3:
            c.execute("""CREATE TABLE IF NOT EXISTS gallery_metadata_index(
              image_id INTEGER PRIMARY KEY,size INTEGER NOT NULL,mtime REAL NOT NULL,processed_at REAL NOT NULL,
              width INTEGER,height INTEGER,taken_text TEXT,taken_epoch REAL,camera_make TEXT,camera_model TEXT,
              exif_orientation INTEGER,gps_lat REAL,gps_lon REAL,gps_alt REAL,error TEXT,
              FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
            c.execute("CREATE INDEX IF NOT EXISTS gallery_metadata_taken_idx ON gallery_metadata_index(taken_epoch)")
            c.execute("CREATE INDEX IF NOT EXISTS gallery_metadata_camera_idx ON gallery_metadata_index(camera_make,camera_model)")
            c.execute("""CREATE TABLE IF NOT EXISTS gallery_smart_albums(
              album_id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE UNIQUE,created_at REAL NOT NULL,updated_at REAL NOT NULL,criteria TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS gallery_tags(
              image_id INTEGER NOT NULL,tag TEXT NOT NULL COLLATE NOCASE,created_at REAL NOT NULL,
              PRIMARY KEY(image_id,tag),FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
            c.execute("CREATE INDEX IF NOT EXISTS gallery_tags_tag_idx ON gallery_tags(tag)")
            c.execute("INSERT OR REPLACE INTO photo_schema_migrations(version,applied_at,note) VALUES(3,?,?)",(time.time(),"metadata intelligence and smart albums"))
            c.execute("PRAGMA user_version=3")
        c.commit()
        integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
    return {"ok":integrity=="ok","from":current,"to":TARGET_VERSION,"backup":str(backup) if backup else None,"integrity":integrity}
