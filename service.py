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

def _owned_pipeline(pid: int, expected: Path) -> tuple[int, int]:
    """Verify exact script, UID, parent and process start ticks before signaling."""
    proc=Path("/proc")/str(pid)
    if proc.stat().st_uid!=os.getuid():
        raise RuntimeError("scheduler UID mismatch")
    stat=(proc/"stat").read_text().rsplit(") ",1)[1].split()
    if stat[0]=="Z":
        return -1,int(stat[19])
    argv=[item.decode(errors="replace") for item in
          (proc/"cmdline").read_bytes().split(b"\x00") if item]
    if len(argv)<2 or Path(argv[1]).resolve()!=expected.resolve():
        raise RuntimeError("scheduler executable identity mismatch")
    return int(stat[1]),int(stat[19])


def _same_live_process(pid: int, start_ticks: int) -> bool:
    """Polling after a signal must tolerate transient empty zombie cmdlines."""
    try:
        stat=(Path("/proc")/str(pid)/"stat").read_text().rsplit(") ",1)[1].split()
        return stat[0]!="Z" and int(stat[19])==start_ticks
    except (OSError,ValueError,IndexError):
        return False


def recover_orphaned_pipeline() -> bool:
    """Reap only an orphan from a previous immutable addon release.

    A short SIGTERM grace period is followed by a checked process-group kill
    for a non-cooperative rish/OCR read. In-progress SQLite changes are atomic;
    source media are never written or deleted.
    """
    pid_file=STATE_DIR/"pipeline_scheduler.pid"
    try:
        pid=int(pid_file.read_text().strip())
    except (OSError,ValueError):
        return False
    proc=Path("/proc")/str(pid)
    if not proc.exists():
        return False
    try:
        argv=[v.decode(errors="replace") for v in
              (proc/"cmdline").read_bytes().split(b"\x00") if v]
        if len(argv)<2:
            return False
        script=Path(argv[1]).resolve()
    except OSError:
        return False
    expected=(ROOT/"runtime/pipeline_scheduler.py").resolve()
    if script==expected:
        return False
    # Only an immutable older release of THIS addon qualifies.
    previous=script.parent.parent
    releases=ROOT.parent.resolve()
    if (releases.name!="releases" or previous.parent!=releases
            or script!=previous/"runtime/pipeline_scheduler.py"
            or not previous.is_dir() or previous==ROOT.resolve()):
        raise RuntimeError("unknown scheduler owns gallery lock")
    meta=json.loads((previous/"addon.json").read_text())
    if (meta.get("name")!="photo-intelligence" or
            meta.get("version")!=previous.name):
        raise RuntimeError("previous scheduler manifest invalid")
    ppid,ticks=_owned_pipeline(pid,script)
    if ppid==-1:
        return False
    if ppid!=1 or os.getpgid(pid)!=pid:
        raise RuntimeError("scheduler is managed elsewhere; refusing to signal")
    os.killpg(pid,signal.SIGTERM)
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        if not _same_live_process(pid,ticks):
            return True
        time.sleep(.1)
    # Non-cooperative child: reverify identity and process group before killing.
    current_parent,current_ticks=_owned_pipeline(pid,script)
    if current_ticks!=ticks or current_parent!=1 or os.getpgid(pid)!=pid:
        raise RuntimeError("scheduler identity changed while retiring")
    os.killpg(pid,signal.SIGKILL)
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        if not _same_live_process(pid,ticks):
            return True
        time.sleep(.1)
    raise RuntimeError("old pipeline did not retire")


def _pipeline_ready(children):
    p=children.get("pipeline")
    if p is None or p.poll() is not None:
        return False
    try:
        current=int((STATE_DIR/"pipeline_scheduler.pid").read_text().strip())
        if current!=p.pid:
            return False
        parent,_=_owned_pipeline(current,(ROOT/"runtime/pipeline_scheduler.py").resolve())
        return parent!=-1
    except (OSError,ValueError,RuntimeError):
        return False


def write_status(children):
    ready=_pipeline_ready(children)
    payload={"ok":True,"state":"STOPPING" if STOP else ("RUNNING" if ready else "DEGRADED"),"pid":os.getpid(),"timestamp":time.time(),"children":{k:{"pid":p.pid,"running":(p.poll() is None and (k!="pipeline" or ready))} for k,p in children.items()}}
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
        specs={"pipeline":(RUNTIME/"pipeline_scheduler.py","pipeline.log"),"watcher":(RUNTIME/"source_watcher.py","watcher.log"),"dashboard":(RUNTIME/"dashboard_server.py","dashboard.log")}
        try:
            recover_orphaned_pipeline()
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
