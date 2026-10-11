#!/usr/bin/env python3
"""Near-real-time local library change detector.
Author: @dvkrum
Uses inotify when available; falls back to lightweight directory timestamp polling.
It never reads image contents and never mutates source files.
"""
from __future__ import annotations
import fcntl,json,os,select,shutil,signal,subprocess,time
from pathlib import Path
from photo_config import SHARED_ROOT,STATE_DIR,ensure_dirs
from photo_settings import effective
LOCK=STATE_DIR/"source_watcher.lock";PID=STATE_DIR/"source_watcher.pid";STATUS=STATE_DIR/"source_watcher_status.json";DIRTY=STATE_DIR/"source_dirty.json";STOP=False
CANDIDATES=("DCIM","Pictures","Download","Android/media","Telegram","WhatsApp")

def stop(*_):
    global STOP;STOP=True

def atomic(path,payload):
    tmp=path.with_name("."+path.name+".tmp");tmp.write_text(json.dumps(payload,separators=(",",":"))+"\n");tmp.chmod(0o600);os.replace(tmp,path)

def mark(reason,path=""):
    # Store only a relative path hint; never file content.
    try: rel=str(Path(path).resolve().relative_to(SHARED_ROOT)) if path else ""
    except Exception: rel=""
    atomic(DIRTY,{"dirty":True,"timestamp":time.time(),"reason":reason,"path":rel[:500]})

def roots():
    out=[]
    for rel in CANDIDATES:
        p=(SHARED_ROOT/rel).resolve()
        if p.is_dir() and p.is_relative_to(SHARED_ROOT):out.append(p)
    return out

def status(mode,watched,last_event=None,error=None):
    atomic(STATUS,{"ok":error is None,"state":"RUNNING" if not STOP else "STOPPING","mode":mode,"watched":[str(x) for x in watched],"last_event":last_event,"error":error,"timestamp":time.time()})

def inotify_loop(watched):
    cmd=[shutil.which("inotifywait"),"-m","-r","-q","-e","close_write,create,moved_to,delete,moved_from","--format","%e|%w%f",*[str(x) for x in watched]]
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,start_new_session=True)
    last_event=None
    status("inotify",watched,last_event)
    try:
        while not STOP:
            if proc.poll() is not None:raise RuntimeError("inotifywait exited")
            ready,_,_=select.select([proc.stdout],[],[],10.0) if proc.stdout else ([],[],[])
            if not ready:
                status("inotify",watched,last_event);continue
            line=proc.stdout.readline()
            if not line:continue
            event,path=(line.rstrip("\n").split("|",1)+[""])[:2]
            # Exchange files and hidden addon transport are not library changes.
            if "/.termuxbridge-photo-intelligence/" in path:continue
            last_event={"event":event,"timestamp":time.time()};mark("inotify:"+event,path);status("inotify",watched,last_event)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill()

def poll_loop(watched):
    mtimes={}
    status("poll",watched)
    while not STOP:
        current={}
        for p in watched:
            try:current[str(p)]=p.stat().st_mtime_ns
            except OSError:continue
        if mtimes and current!=mtimes:mark("directory_mtime")
        mtimes=current;status("poll",watched,{"timestamp":time.time()})
        for _ in range(30):
            if STOP:break
            time.sleep(.5)

def main():
    ensure_dirs()
    with LOCK.open("a+") as fp:
        try:fcntl.flock(fp,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        PID.write_text(str(os.getpid())+"\n");PID.chmod(0o600);signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        watched=roots()
        try:
            if not effective().get("watch_local",True):
                status("disabled",watched)
                while not STOP:time.sleep(2)
            elif shutil.which("inotifywait") and watched:
                try:inotify_loop(watched)
                except Exception as exc:
                    status("fallback",watched,error=type(exc).__name__);poll_loop(watched)
            else:poll_loop(watched)
        finally:
            try:
                if PID.exists() and PID.read_text().strip()==str(os.getpid()):PID.unlink()
            except OSError:pass
    return 0
if __name__=="__main__":raise SystemExit(main())
