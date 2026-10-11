#!/data/data/com.termux/files/usr/bin/python
"""Continuous incremental gallery maintenance for Photo Intelligence.
Author: @dvkrum
Scans metadata frequently and indexes new OCR content without source mutations.
"""
from __future__ import annotations
import fcntl,json,os,sys,time
from pathlib import Path
from photo_config import STATE_DIR
SCAN_INTERVAL_SECONDS=300

def run() -> dict:
    import gallery_bridge_tools as g
    root=STATE_DIR; root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fs=os.statvfs(root)
    if fs.f_bavail*fs.f_frsize < 512*1024*1024:
        return {"ok":True,"result":"SKIPPED_STORAGE","minimum_free_bytes":512*1024*1024}
    state=g.gallery_status({}); scan={"ok":True,"result":"NOT_DUE"}
    if not state.get("last_scan") or time.time()-float(state["last_scan"])>=SCAN_INTERVAL_SECONDS:
        scan=g.gallery_scan({})
    if not scan.get("ok"):
        return {"ok":False,"result":"SCAN_FAILED","scan":scan}
    try:
        ocr=g.gallery_ocr_index({"max_images":16,"max_seconds":120,"languages":"rus+lav+eng","allow_on_battery":True,"unrestricted":True})
    except Exception as exc:
        return {"ok":False,"result":"OCR_ERROR","error":type(exc).__name__,"scan":scan}
    return {"ok":bool(ocr.get("ok")),"result":"OK" if ocr.get("ok") else "OCR_UNAVAILABLE","scan":scan,"ocr":ocr}

def main() -> int:
    private=STATE_DIR;private.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (private/"gallery_maintenance.lock").open("a+") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        try:result=run()
        except Exception as exc:result={"ok":False,"result":"ERROR","error":type(exc).__name__}
        result["timestamp"]=time.time();status=private/"gallery_maintenance_status.json";tmp=private/".gallery_maintenance_status.json.tmp";tmp.write_text(json.dumps(result,ensure_ascii=False)+"\n");tmp.chmod(0o600);os.replace(tmp,status)
    return 0 if result["ok"] else 1
if __name__=="__main__":raise SystemExit(main())
