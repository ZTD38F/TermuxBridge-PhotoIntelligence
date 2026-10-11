"""Persistent runtime settings and processing profiles.
Author: @dvkrum
"""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any
from photo_config import STATE_DIR

SETTINGS=STATE_DIR/"settings.json"
PROFILES={
    "balanced":{"scan_interval":300,"metadata_images":64,"taxonomy_images":128,"ocr_images":48,"ocr_seconds":180,"visual_images":24,"duplicate_images":48,"duplicate_mb":256,"semantic_images":2,"idle_sleep":5.0,"busy_sleep":0.15,"thermal_pause_c":45.0,"min_battery":15},
    "fast":{"scan_interval":120,"metadata_images":128,"taxonomy_images":256,"ocr_images":96,"ocr_seconds":360,"visual_images":48,"duplicate_images":96,"duplicate_mb":512,"semantic_images":4,"idle_sleep":2.0,"busy_sleep":0.05,"thermal_pause_c":47.0,"min_battery":10},
    "unlimited":{"scan_interval":60,"metadata_images":256,"taxonomy_images":512,"ocr_images":128,"ocr_seconds":900,"visual_images":96,"duplicate_images":192,"duplicate_mb":1024,"semantic_images":8,"idle_sleep":1.0,"busy_sleep":0.02,"thermal_pause_c":55.0,"min_battery":1},
}
DEFAULT={"profile":"balanced","languages":"rus+lav+eng","watch_local":True,"auto_scan":True,"auto_metadata":True,"auto_ocr":True,"auto_visual":True,"auto_duplicates":True,"auto_semantic":True,"auto_taxonomy":True,"dashboard_token_required":False}
ALLOWED=set(DEFAULT)

def _atomic(path:Path,payload:dict[str,Any]):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_name("."+path.name+".tmp");tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n");tmp.chmod(0o600);os.replace(tmp,path)

def load()->dict[str,Any]:
    value=dict(DEFAULT)
    try:
        raw=json.loads(SETTINGS.read_text())
        if isinstance(raw,dict): value.update({k:v for k,v in raw.items() if k in ALLOWED})
    except Exception:pass
    if value.get("profile") not in PROFILES:value["profile"]="balanced"
    return value

def effective()->dict[str,Any]:
    value=load();return {**PROFILES[value["profile"]],**value}

def update(changes:dict[str,Any])->dict[str,Any]:
    unknown=set(changes)-ALLOWED
    if unknown:raise ValueError("unknown settings: "+",".join(sorted(unknown)))
    value=load()
    if "profile" in changes and changes["profile"] not in PROFILES:raise ValueError("invalid profile")
    if "languages" in changes:
        langs=str(changes["languages"])
        allowed={"eng","lav","rus"}; parts=[x for x in langs.split('+') if x]
        if not parts or any(x not in allowed for x in parts):raise ValueError("invalid OCR languages")
    for key,val in changes.items():
        if key.startswith("auto_") or key in {"watch_local","dashboard_token_required"}:
            if not isinstance(val,bool):raise ValueError(key+" must be boolean")
        value[key]=val
    _atomic(SETTINGS,value);return effective()
