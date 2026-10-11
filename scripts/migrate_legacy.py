#!/usr/bin/env python3
"""Import the legacy in-core gallery state into the standalone addon.
Author: @dvkrum
The legacy source is never removed.
"""
from __future__ import annotations
import json, os, shutil, sqlite3, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"runtime"))
from photo_config import STATE_DIR,LEGACY_STATE_DIR,ensure_dirs
FILES=("gallery_full_ocr_status.json","gallery_maintenance_status.json")

def main():
    ensure_dirs(); src=LEGACY_STATE_DIR/"gallery.sqlite3"; dst=STATE_DIR/"gallery.sqlite3"
    result={"ok":True,"database":"none","state_files":[],"legacy_preserved":True,"timestamp":time.time()}
    if src.is_file() and not dst.exists():
        target=sqlite3.connect(dst); source=sqlite3.connect(f"file:{src}?mode=ro",uri=True,timeout=10)
        try: source.backup(target)
        finally: target.close(); source.close()
        dst.chmod(0o600); result["database"]="imported"
    elif dst.is_file(): result["database"]="already_present"
    for name in FILES:
        s=LEGACY_STATE_DIR/name; d=STATE_DIR/name
        if s.is_file() and not d.exists():
            shutil.copy2(s,d); d.chmod(0o600); result["state_files"].append(name)
    # Full coverage should continue automatically after migration.
    enabled=STATE_DIR/"gallery_full_ocr.enabled"; enabled.touch(exist_ok=True); enabled.chmod(0o600)
    marker=STATE_DIR/"legacy_migration.json"; tmp=marker.with_name("."+marker.name+".tmp"); tmp.write_text(json.dumps(result,separators=(",",":"))+"\n"); tmp.chmod(0o600); os.replace(tmp,marker)
    print(json.dumps(result))
    return 0
if __name__=="__main__": raise SystemExit(main())
