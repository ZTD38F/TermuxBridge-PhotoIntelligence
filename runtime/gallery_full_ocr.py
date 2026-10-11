#!/usr/bin/env python3
"""Complete Photo Intelligence gallery OCR coverage runner.

Author: @dvkrum.
Processes every current indexed image to a terminal OCR state (success or recorded
failure). Never modifies source photos. Resumes safely after interruption.
"""
from __future__ import annotations
import fcntl, json, os, time
from pathlib import Path
import gallery_bridge_tools as g
from photo_config import STATE_DIR

STATE=STATE_DIR
STATUS=STATE/'gallery_full_ocr_status.json'
LOCK=STATE/'gallery_full_ocr.lock'
PID=STATE/'gallery_full_ocr.pid'
ENABLED=STATE/'gallery_full_ocr.enabled'
LANG='rus+lav+eng'

def snapshot():
    with g._db() as c:
        total=c.execute('SELECT COUNT(*) FROM images').fetchone()[0]
        done=c.execute('''SELECT COUNT(*) FROM gallery_ocr_index o JOIN images i ON i.id=o.image_id
                          WHERE o.size=i.size AND o.mtime=i.mtime AND o.languages=?''',(LANG,)).fetchone()[0]
        ok=c.execute('''SELECT COUNT(*) FROM gallery_ocr_index o JOIN images i ON i.id=o.image_id
                        WHERE o.size=i.size AND o.mtime=i.mtime AND o.languages=? AND o.error IS NULL''',(LANG,)).fetchone()[0]
        failed=done-ok
    return total,done,ok,failed

def write_status(result, state='RUNNING'):
    total,done,ok,failed=snapshot()
    try: source_progress=g.gallery_ocr_status({}).get("ocr_sources",{})
    except Exception: source_progress={}
    payload={"ok":True,"state":state,"total":total,"processed":done,"recognized":ok,"failed":failed,
             "remaining":max(0,total-done),"percent":round(done*100/total,2) if total else 100.0,
             "sources":source_progress,"deferred_sources":result.get("deferred_sources",[]),
             "last_batch":result,"timestamp":time.time()}
    tmp=STATUS.with_name('.'+STATUS.name+'.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False)+'\n')
    tmp.chmod(0o600); os.replace(tmp,STATUS)
    return payload

def main():
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with LOCK.open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return 0
        PID.write_text(str(os.getpid())+'\n',encoding='ascii'); PID.chmod(0o600)
        ENABLED.touch(exist_ok=True); ENABLED.chmod(0o600)
        initial=g.gallery_status({})
        if not initial.get("indexed") or not initial.get("last_scan"):
            scan=g.gallery_scan({})
            if not scan.get("ok"):
                write_status({"scan":scan},'PAUSED'); return 2
        write_status({},'STARTING')
        try:
            while True:
                total,done,ok,failed=snapshot()
                if done>=total:
                    write_status({},'COMPLETE'); return 0
                result=g.gallery_ocr_index({"max_images":128,"max_seconds":3600,
                                            "languages":LANG,"allow_on_battery":True,
                                            "unrestricted":True})
                paused = result.get('paused')
                try:
                    current=g.gallery_status({})
                    if not current.get("last_scan") or time.time()-float(current["last_scan"])>=300:
                        result["incremental_scan"]=g.gallery_scan({})
                except Exception as exc:
                    result["incremental_scan"]={"ok":False,"error":type(exc).__name__}
                write_status(result,'PAUSED' if (not result.get('ok') or paused) else 'RUNNING')
                if not result.get('ok') or paused:
                    time.sleep(60 if paused == "SHIZUKU_UNAVAILABLE" else 2)
                    continue
                if not result.get('recognized_ids') and not result.get('errors'):
                    g.gallery_scan({})
                    total2,done2,_,_=snapshot()
                    if done2>=total2:
                        write_status({},'COMPLETE'); return 0
                    time.sleep(5)
                else:
                    time.sleep(0.01)
        finally:
            try:
                if PID.exists() and PID.read_text().strip()==str(os.getpid()):
                    PID.unlink()
            except OSError:
                pass

if __name__=='__main__':
    raise SystemExit(main())
