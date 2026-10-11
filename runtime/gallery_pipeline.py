"""Pipeline/settings/health MCP surface for Photo Intelligence.
Author: @dvkrum
"""
from __future__ import annotations
import json,os,time
from pathlib import Path
from typing import Any
from photo_config import STATE_DIR
from photo_settings import effective,load,update,PROFILES

def _read(path:Path,default):
    try:return json.loads(path.read_text())
    except Exception:return default

def _atomic(path:Path,payload):
    tmp=path.with_name("."+path.name+".tmp");tmp.write_text(json.dumps(payload,separators=(",",":"))+"\n");tmp.chmod(0o600);os.replace(tmp,path)

def install(g:dict[str,Any])->None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"]
    def settings_get(_):return {"ok":True,"settings":load(),"effective":effective(),"profiles":PROFILES}
    def settings_update(args):
        changes=args.get("changes")
        if not isinstance(changes,dict) or not changes:raise ValueError("changes object required")
        return {"ok":True,"effective":update(changes)}
    def pipeline_status(_):
        p=_read(STATE_DIR/"pipeline_status.json",{"ok":False,"state":"NOT_STARTED"})
        w=_read(STATE_DIR/"source_watcher_status.json",{"ok":False,"state":"NOT_STARTED"})
        return {"ok":True,"pipeline":p,"watcher":w}
    def metrics(_):return {"ok":True,"metrics":_read(STATE_DIR/"pipeline_metrics.json",{})}
    def reconcile(args):
        reason=str(args.get("reason","manual"))[:80]
        _atomic(STATE_DIR/"source_dirty.json",{"dirty":True,"timestamp":time.time(),"reason":"manual:"+reason,"path":""})
        return {"ok":True,"queued":True,"reason":reason}
    def health(_):
        pipe=_read(STATE_DIR/"pipeline_status.json",{});svc=_read(STATE_DIR/"service_status.json",{});watch=_read(STATE_DIR/"source_watcher_status.json",{})
        db=g["gallery_status"]({});src=tools["gallery_source_status"][1]({});sem=tools["gallery_semantic_status"][1]({})
        now=time.time();issues=[]
        if db.get("db_integrity")!="ok":issues.append("DATABASE_INTEGRITY")
        if not pipe or now-float(pipe.get("timestamp",0))>120:issues.append("PIPELINE_STALE")
        if not svc or now-float(svc.get("timestamp",0))>120:issues.append("SERVICE_STALE")
        if effective().get("watch_local") and (not watch or now-float(watch.get("timestamp",0))>120):issues.append("WATCHER_STALE")
        if not sem.get("available"):issues.append("SEMANTIC_MODEL_UNAVAILABLE")
        deferred=src.get("deferred_sources",[])
        return {"ok":not any(x in issues for x in ("DATABASE_INTEGRITY","PIPELINE_STALE","SERVICE_STALE")),"state":"HEALTHY" if not issues and not deferred else "DEGRADED" if issues or deferred else "HEALTHY","issues":issues,"deferred_sources":deferred,"database":db.get("db_integrity"),"pipeline":pipe.get("state","NOT_STARTED"),"profile":effective()["profile"],"library":db.get("indexed",0),"timestamp":now}
    tools["gallery_settings_get"]=(schema(),settings_get)
    tools["gallery_settings_update"]=(schema({"changes":{"type":"object","additionalProperties":True}},["changes"]),settings_update)
    tools["gallery_pipeline_status"]=(schema(),pipeline_status)
    tools["gallery_pipeline_metrics"]=(schema(),metrics)
    tools["gallery_reconcile"]=(schema({"reason":{"type":"string","maxLength":80,"default":"manual"}}),reconcile)
    tools["gallery_health"]=(schema(),health)
    descriptions.update({
        "gallery_settings_get":"Read Photo Intelligence processing profile and runtime settings.",
        "gallery_settings_update":"Update Photo Intelligence processing profile/settings. Does not modify source photos.",
        "gallery_pipeline_status":"Read unified scheduler and source watcher status.",
        "gallery_pipeline_metrics":"Read aggregate per-stage processing metrics.",
        "gallery_reconcile":"Queue an immediate metadata reconciliation scan.",
        "gallery_health":"Read concise Photo Intelligence health, deferred sources and active profile."
    })
    readonly.update({"gallery_settings_get","gallery_pipeline_status","gallery_pipeline_metrics","gallery_health"})
