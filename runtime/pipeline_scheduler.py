#!/usr/bin/env python3
"""Unified resumable Photo Intelligence processing scheduler.
Author: @dvkrum

One scheduler owns scan/OCR/visual/duplicate/semantic sequencing. Individual
engines remain independently resumable through SQLite, so a process restart
never loses completed work.
"""
from __future__ import annotations
import fcntl,json,os,signal,subprocess,time
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FutureTimeout
from pathlib import Path
import gallery_bridge_tools as g
from photo_config import STATE_DIR,ensure_dirs
from photo_settings import effective
LOCK=STATE_DIR/"pipeline_scheduler.lock";PID=STATE_DIR/"pipeline_scheduler.pid";STATUS=STATE_DIR/"pipeline_status.json";METRICS=STATE_DIR/"pipeline_metrics.json";DIRTY=STATE_DIR/"source_dirty.json";STOP=False


def stop(*_):
    global STOP;STOP=True

def atomic(path,payload):
    tmp=path.with_name("."+path.name+".tmp");tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":"))+"\n");tmp.chmod(0o600);os.replace(tmp,path)

def read_json(path,default):
    try:
        value=json.loads(path.read_text());return value if isinstance(value,dict) else default
    except Exception:return default

def battery():
    try:
        p=subprocess.run(["termux-battery-status"],capture_output=True,text=True,timeout=5,check=False)
        d=json.loads(p.stdout) if p.returncode==0 else {}
        return {"available":bool(d),"level":int(d.get("percentage",100)),"charging":str(d.get("status","")).upper()=="CHARGING" or str(d.get("plugged","")).upper() not in {"UNPLUGGED","UNKNOWN",""},"temperature_c":float(d.get("temperature",0.0))}
    except Exception:return {"available":False,"level":100,"charging":False,"temperature_c":0.0}

def power_allowed(cfg,b):
    if cfg["profile"]=="unlimited" or not b.get("available"):return True,None
    if b["temperature_c"] and b["temperature_c"]>=float(cfg["thermal_pause_c"]):return False,"THERMAL"
    if not b["charging"] and b["level"]<int(cfg["min_battery"]):return False,"BATTERY"
    return True,None

def due_scan(cfg,last_scan):
    if not cfg.get("auto_scan",True):return False,"disabled"
    if DIRTY.exists():return True,"event"
    if not last_scan or time.time()-float(last_scan)>=int(cfg["scan_interval"]):return True,"reconcile"
    return False,"not_due"

def summarize(result):
    if not isinstance(result,dict):return {"ok":False,"error":"invalid_result"}
    out={"ok":bool(result.get("ok",False))}
    for key in ("error","result","paused","deferred_sources","shizuku_available","shizuku_pending","indexed","scanned","removed_missing","read_errors","seconds","hashed_megabytes","engine","model"):
        if key in result:out[key]=result[key]
    for src,dst in (("recognized_ids","recognized"),("indexed_ids","indexed_items"),("errors","errors")):
        value=result.get(src)
        if isinstance(value,list):out[dst]=len(value)
    return out

def _heartbeat(stage,cfg,b):
    current=read_json(STATUS,{})
    current.update({"ok":True,"state":"RUNNING","phase":stage,"profile":cfg.get("profile","balanced"),"pid":os.getpid(),"battery":b,"timestamp":time.time()})
    atomic(STATUS,current)

def run_stage(name,fn,metrics,cfg,b):
    started=time.monotonic();result=None
    executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="photo-"+name)
    future=executor.submit(fn)
    try:
        while True:
            try:
                result=future.result(timeout=5.0);break
            except FutureTimeout:
                _heartbeat(name,cfg,b)
        ok=bool(result.get("ok",False)) if isinstance(result,dict) else False
    except Exception as exc:
        result={"ok":False,"error":type(exc).__name__};ok=False
    finally:
        executor.shutdown(wait=True,cancel_futures=False)
    elapsed=round(time.monotonic()-started,3)
    bucket=metrics.setdefault("stages",{}).setdefault(name,{"runs":0,"success":0,"failures":0,"seconds":0.0})
    bucket["runs"]+=1;bucket["success" if ok else "failures"]+=1;bucket["seconds"]=round(float(bucket.get("seconds",0.0))+elapsed,3);bucket["last_at"]=time.time();bucket["last_ok"]=ok;bucket["last_seconds"]=elapsed
    return result,elapsed

def coverage():
    o=g.gallery_ocr_status({});v=g.TOOLS["gallery_visual_status"][1]({});s=g.TOOLS["gallery_semantic_status"][1]({});d=g.TOOLS["gallery_duplicate_status"][1]({});src=g.TOOLS["gallery_source_status"][1]({})
    total=int(o.get("indexed",0) or 0);processed=int(o.get("ocr_indexed",0) or 0)+int(o.get("ocr_failed",0) or 0)
    return {"library":total,"ocr":{"processed":processed,"remaining":max(0,total-processed),"percent":round(processed*100/total,2) if total else 100.0,"sources":o.get("ocr_sources",{})},"visual":v,"semantic":s,"duplicates":d,"sources":src}

def write_legacy_ocr(cov,last_batch,state):
    ocr=cov["ocr"]; status={"ok":True,"state":state,"total":cov["library"],"processed":ocr["processed"],"recognized":max(0,ocr["processed"]-int(g.gallery_ocr_status({}).get("ocr_failed",0) or 0)),"failed":int(g.gallery_ocr_status({}).get("ocr_failed",0) or 0),"remaining":ocr["remaining"],"percent":ocr["percent"],"sources":ocr.get("sources",{}),"last_batch":last_batch,"timestamp":time.time()}
    atomic(STATE_DIR/"gallery_full_ocr_status.json",status)

def main():
    ensure_dirs();metrics=read_json(METRICS,{"started_at":time.time(),"cycles":0,"stages":{}})
    with LOCK.open("a+") as fp:
        try:fcntl.flock(fp,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        PID.write_text(str(os.getpid())+"\n");PID.chmod(0o600);signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try:
            while not STOP:
                cycle_start=time.monotonic();cfg=effective();b=battery();allowed,pause_reason=power_allowed(cfg,b);last={};work=False
                base=g.gallery_status({});scan_needed,scan_reason=due_scan(cfg,base.get("last_scan"))
                if scan_needed:
                    result,_=run_stage("scan",lambda:g.gallery_scan({}),metrics,cfg,b);last["scan"]={**summarize(result),"reason":scan_reason};work=True
                    if result.get("ok"):
                        try:DIRTY.unlink()
                        except FileNotFoundError:pass
                # Metadata scan is always allowed. Heavy stages honor the selected public profile.
                if allowed:
                    if cfg.get("auto_ocr",True):
                        result,_=run_stage("ocr",lambda:g.gallery_ocr_index({"max_images":int(cfg["ocr_images"]),"max_seconds":int(cfg["ocr_seconds"]),"languages":cfg["languages"],"allow_on_battery":True,"unrestricted":True}),metrics,cfg,b);last["ocr"]=summarize(result);work=work or bool(result.get("recognized_ids") or result.get("errors"))
                    if cfg.get("auto_visual",True):
                        result,_=run_stage("visual",lambda:g.TOOLS["gallery_visual_index"][1]({"limit":int(cfg["visual_images"])}),metrics,cfg,b);last["visual"]=summarize(result);work=work or bool(result.get("indexed_ids"))
                    if cfg.get("auto_duplicates",True):
                        result,_=run_stage("duplicates",lambda:g.TOOLS["gallery_duplicate_index"][1]({"limit":int(cfg["duplicate_images"]),"max_megabytes":int(cfg["duplicate_mb"])}),metrics,cfg,b);last["duplicates"]=summarize(result);work=work or bool(result.get("indexed_ids"))
                    if cfg.get("auto_semantic",True):
                        result,_=run_stage("semantic",lambda:g.TOOLS["gallery_semantic_index"][1]({"limit":int(cfg["semantic_images"])}),metrics,cfg,b);last["semantic"]=summarize(result);work=work or bool(result.get("indexed_ids"))
                cov=coverage();deferred=cov["sources"].get("deferred_sources",[]);all_done=(cov["ocr"]["remaining"]==0 and int(cov["visual"].get("remaining",0))==0 and int(cov["semantic"].get("remaining",0))==0 and int(cov["duplicates"].get("remaining",0))==0)
                if not allowed:state="POWER_PAUSED"
                elif all_done:state="COMPLETE"
                elif work:state="RUNNING"
                elif deferred:state="DEFERRED_SOURCE"
                else:state="IDLE"
                metrics["cycles"]=int(metrics.get("cycles",0))+1;metrics["last_cycle_seconds"]=round(time.monotonic()-cycle_start,3);metrics["last_cycle_at"]=time.time();atomic(METRICS,metrics)
                payload={"ok":True,"state":state,"phase":None,"profile":cfg["profile"],"pid":os.getpid(),"battery":b,"power_pause_reason":pause_reason,"coverage":cov,"last":last,"cycle_seconds":metrics["last_cycle_seconds"],"dirty":DIRTY.exists(),"timestamp":time.time()};atomic(STATUS,payload)
                write_legacy_ocr(cov,last.get("ocr",{}),"COMPLETE" if cov["ocr"]["remaining"]==0 else ("PAUSED" if not allowed else "RUNNING"))
                delay=float(cfg["busy_sleep"] if work else cfg["idle_sleep"]);end=time.monotonic()+delay
                while not STOP and time.monotonic()<end:time.sleep(min(.2,max(0,end-time.monotonic())))
        finally:
            try:
                if PID.exists() and PID.read_text().strip()==str(os.getpid()):PID.unlink()
            except OSError:pass
    return 0
if __name__=="__main__":raise SystemExit(main())
