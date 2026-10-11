#!/usr/bin/env python3
"""Continuous visual + semantic indexing worker.
Author: @dvkrum
"""
from __future__ import annotations
import fcntl,json,os,signal,time
from pathlib import Path
import gallery_bridge_tools as g
from photo_config import STATE_DIR,ensure_dirs
LOCK=STATE_DIR/"analysis_worker.lock"; PID=STATE_DIR/"analysis_worker.pid"; STATUS=STATE_DIR/"analysis_worker_status.json"; STOP=False

def stop(*_):
    global STOP; STOP=True

def write(payload):
    payload={**payload,"timestamp":time.time()}; tmp=STATUS.with_name("."+STATUS.name+".tmp"); tmp.write_text(json.dumps(payload,separators=(",",":"))+"\n"); tmp.chmod(0o600); os.replace(tmp,STATUS)

def main():
    ensure_dirs()
    with LOCK.open("a+") as fp:
        try:fcntl.flock(fp,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        PID.write_text(str(os.getpid())+"\n");PID.chmod(0o600);signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try:
            while not STOP:
                visual=g.TOOLS["gallery_visual_status"][1]({}); semantic=g.TOOLS["gallery_semantic_status"][1]({}); duplicates=g.TOOLS["gallery_duplicate_status"][1]({}); work=False; last={}
                if visual.get("remaining",0)>0:
                    last["visual"]=g.TOOLS["gallery_visual_index"][1]({"limit":24});work=True
                if duplicates.get("remaining",0)>0:
                    last["duplicates"]=g.TOOLS["gallery_duplicate_index"][1]({"limit":48,"max_megabytes":256});work=True
                if semantic.get("available") and semantic.get("remaining",0)>0:
                    last["semantic"]=g.TOOLS["gallery_semantic_index"][1]({"limit":2});work=True
                visual2=g.TOOLS["gallery_visual_status"][1]({});semantic2=g.TOOLS["gallery_semantic_status"][1]({});duplicates2=g.TOOLS["gallery_duplicate_status"][1]({})
                write({"ok":True,"state":"RUNNING" if work else ("WAITING_MODEL" if not semantic2.get("available") else "COMPLETE"),"visual":visual2,"semantic":semantic2,"duplicates":duplicates2,"last":last})
                delay=0.2 if work else 30
                end=time.time()+delay
                while not STOP and time.time()<end:time.sleep(min(0.2,max(0,end-time.time())))
        finally:
            try:
                if PID.exists() and PID.read_text().strip()==str(os.getpid()):PID.unlink()
            except OSError:pass
    return 0
if __name__=="__main__":raise SystemExit(main())
