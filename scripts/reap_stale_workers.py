#!/usr/bin/env python3
"""Reap orphaned Photo Intelligence workers after a release transition.
Author: @dvkrum

Run only after the normal TermuxBridge/addon shutdown path. Targets are restricted
to the Photo Intelligence addon root, current Termux UID and an exact executable
basename allowlist. SIGTERM is attempted first; SIGKILL is a bounded fallback for
verified unchanged stale identities only.
"""
from __future__ import annotations
import json,os,signal,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"runtime"))
from photo_config import HOME,STATE_DIR,ensure_dirs
TARGETS={"service.py","gallery_worker.py","gallery_full_ocr.py","analysis_worker.py","dashboard_server.py","gallery_maintenance.py"}

def inspect(pid:int):
    p=Path('/proc')/str(pid)
    try:
        if p.stat().st_uid!=os.getuid():return None
        argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x]
        if len(argv)<2:return None
        fields=(p/'stat').read_text().rsplit(') ',1)[1].split();start_ticks=int(fields[19]);state=fields[0]
        if state=='Z':return None
        script=Path(argv[1]).expanduser().resolve();root=HOME.resolve()
        if script.name not in TARGETS or not script.is_relative_to(root):return None
        return {"pid":pid,"script":str(script),"name":script.name,"start_ticks":start_ticks,"pgid":os.getpgid(pid)}
    except (OSError,ValueError,ProcessLookupError):return None

def same_identity(item):
    current=inspect(int(item['pid']))
    return bool(current and current['start_ticks']==item['start_ticks'] and current['script']==item['script'])

def discover():
    out=[]
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            item=inspect(int(p.name))
            if item:out.append(item)
    return out

def signal_groups(items,sig):
    groups={int(x['pgid']) for x in items if same_identity(x)}
    for pgid in groups:
        try:os.killpg(pgid,sig)
        except ProcessLookupError:pass
    return sorted(groups)

def wait_remaining(items,seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        left=[x for x in items if same_identity(x)]
        if not left:return []
        time.sleep(.1)
    return [x for x in items if same_identity(x)]

def main():
    ensure_dirs();started=time.time();found=discover();term_groups=signal_groups(found,signal.SIGTERM);remaining=wait_remaining(found,8.0)
    kill_groups=[]
    if remaining:
        kill_groups=signal_groups(remaining,signal.SIGKILL);remaining=wait_remaining(remaining,3.0)
    payload={"ok":not remaining,"timestamp":time.time(),"duration_seconds":round(time.time()-started,2),"found":found,
             "sigterm_groups":term_groups,"sigkill_groups":kill_groups,"remaining":remaining,
             "scope":str(HOME.resolve()),"source_photos_modified":False}
    path=STATE_DIR/'stale_worker_reap.json';tmp=path.with_name('.'+path.name+'.tmp');tmp.write_text(json.dumps(payload,separators=(',',':'))+'\n');tmp.chmod(0o600);os.replace(tmp,path)
    print(json.dumps(payload,separators=(',',':')))
    return 0 if payload['ok'] else 2
if __name__=='__main__':raise SystemExit(main())
