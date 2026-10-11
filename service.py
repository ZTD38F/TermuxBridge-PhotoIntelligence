#!/usr/bin/env python3
"""Self-healing local service supervisor for Photo Intelligence.
Author: @dvkrum
"""
from __future__ import annotations
import fcntl,json,os,signal,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT/"runtime"
sys.path.insert(0,str(RUNTIME))
from photo_config import HOME,STATE_DIR,LOG_DIR,ensure_dirs
LOCK=STATE_DIR/"service.lock"; PID=STATE_DIR/"service.pid"; STATUS=STATE_DIR/"service_status.json"
STOP=False

def stop(*_):
    global STOP; STOP=True

def write_status(children):
    payload={"ok":True,"state":"RUNNING" if not STOP else "STOPPING","pid":os.getpid(),"timestamp":time.time(),"children":{k:{"pid":p.pid,"running":p.poll() is None} for k,p in children.items()}}
    tmp=STATUS.with_name("."+STATUS.name+".tmp"); tmp.write_text(json.dumps(payload,separators=(",",":"))+"\n"); tmp.chmod(0o600); os.replace(tmp,STATUS)

def spawn(name,script,log_name):
    log=(LOG_DIR/log_name).open("ab",buffering=0)
    env=os.environ.copy(); env["PYTHONPATH"]=str(RUNTIME); env["PHOTO_INTELLIGENCE_CURRENT"]=str(ROOT)
    p=subprocess.Popen([sys.executable,str(script)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,env=env)
    p._photo_log=log
    return p

def _managed_full_ocr():
    try:
        pid=int((STATE_DIR/"gallery_full_ocr.pid").read_text().strip())
        proc=Path(f"/proc/{pid}")
        argv=[x.decode(errors="replace") for x in (proc/"cmdline").read_bytes().split(b"\0") if x]
        if proc.stat().st_uid!=os.getuid() or len(argv)<2:return None
        script=Path(argv[1]).resolve()
        expected=(ROOT/"runtime"/"gallery_full_ocr.py").resolve()
        return pid if script==expected else None
    except Exception:return None

def terminate_all(children):
    targets=[]
    for p in children.values():
        if p and p.poll() is None:
            try:p.terminate();targets.append(p.pid)
            except ProcessLookupError:pass
    full=_managed_full_ocr()
    if full is not None:
        try:os.kill(full,signal.SIGTERM);targets.append(full)
        except ProcessLookupError:pass
    deadline=time.time()+10
    while time.time()<deadline:
        if not any(Path(f"/proc/{pid}").exists() for pid in targets):break
        time.sleep(.1)
    for p in children.values():
        try:getattr(p,"_photo_log",None).close()
        except Exception:pass
    return [pid for pid in targets if Path(f"/proc/{pid}").exists()]

def main():
    ensure_dirs(); (STATE_DIR/"gallery_full_ocr.enabled").touch(exist_ok=True)
    with LOCK.open("a+") as fp:
        try:fcntl.flock(fp,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        PID.write_text(str(os.getpid())+"\n"); PID.chmod(0o600)
        signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
        wake_lock=False
        if os.environ.get("PHOTO_INTELLIGENCE_DISABLE_WAKE_LOCK","0") != "1" and shutil.which("termux-wake-lock"):
            wake_lock=subprocess.run(["termux-wake-lock"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False).returncode==0
        children={}
        specs={"worker":(RUNTIME/"gallery_worker.py","worker.log"),"analysis":(RUNTIME/"analysis_worker.py","analysis.log"),"dashboard":(RUNTIME/"dashboard_server.py","dashboard.log")}
        try:
            while not STOP:
                for name,(script,log) in specs.items():
                    p=children.get(name)
                    if p is None or p.poll() is not None:
                        if p is not None:
                            try:getattr(p,"_photo_log",None).close()
                            except Exception:pass
                        children[name]=spawn(name,script,log)
                write_status(children)
                for _ in range(10):
                    if STOP: break
                    time.sleep(1)
        finally:
            still_alive=terminate_all(children)
            write_status(children)
            if still_alive:
                # No SIGKILL: leave exact PIDs visible for diagnostics.
                try:
                    data=json.loads(STATUS.read_text());data["shutdown_pending_pids"]=still_alive;STATUS.write_text(json.dumps(data,separators=(",",":"))+"\n")
                except Exception:pass
            if wake_lock and shutil.which("termux-wake-unlock"):
                subprocess.run(["termux-wake-unlock"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            try:
                if PID.exists() and PID.read_text().strip()==str(os.getpid()): PID.unlink()
            except OSError:pass
    return 0
if __name__=="__main__": raise SystemExit(main())
