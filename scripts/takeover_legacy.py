#!/usr/bin/env python3
"""Stop only legacy in-core Photo Intelligence workers before addon takeover.
Author: @dvkrum
Never signals unrelated processes and never uses SIGKILL.
"""
from __future__ import annotations
import json,os,signal,time
from pathlib import Path
BRIDGE=Path(os.environ.get('TERMUXBRIDGE_ROOT',str(Path.home()/'termux-mcp-bridge'))).resolve()
RELEASES=(BRIDGE/'releases').resolve()
NAMES={'gallery_worker.py','gallery_full_ocr.py','gallery_maintenance.py'}

def candidates():
    found=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            if proc.stat().st_uid!=os.getuid():continue
            argv=[x.decode(errors='replace') for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
            if len(argv)<2:continue
            script=Path(argv[1]).expanduser().resolve()
            if script.name not in NAMES:continue
            if not script.is_relative_to(RELEASES):continue
            found.append((int(proc.name),str(script)))
        except (OSError,ValueError):continue
    return found

def main():
    found=candidates();sent=[]
    for pid,script in found:
        try:os.kill(pid,signal.SIGTERM);sent.append({'pid':pid,'script':script})
        except ProcessLookupError:pass
    deadline=time.time()+12
    while time.time()<deadline:
        alive=[]
        for item in sent:
            try:os.kill(item['pid'],0);alive.append(item)
            except ProcessLookupError:pass
        if not alive:break
        time.sleep(.2)
    alive=[]
    for item in sent:
        try:os.kill(item['pid'],0);alive.append(item)
        except ProcessLookupError:pass
    print(json.dumps({'ok':not alive,'signaled':sent,'still_alive':alive,'sigkill_used':False,'bridge':str(BRIDGE)},separators=(',',':')))
    return 0 if not alive else 2
if __name__=='__main__':raise SystemExit(main())
