"""Photo source capability/status layer.
Author: @dvkrum
"""
from __future__ import annotations
from typing import Any
import gallery_shizuku

def install(g: dict[str,Any]) -> None:
    schema=g["_schema"]; tools=g["TOOLS"]; descriptions=g["DESCRIPTIONS"]; readonly=g["READ_ONLY"]; db=g["_db"]
    def status(_):
        with db() as c:
            total=c.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            sh=c.execute("SELECT COUNT(*) FROM images WHERE path LIKE 'shizuku://%'").fetchone()[0]
        sh_available=gallery_shizuku.available_cached(ttl=30.0)
        return {"ok":True,"total":total,
                "sources":{"local_shared_storage":{"indexed":total-sh,"available":True,"state":"READY"},
                           "android_data_shizuku":{"indexed":sh,"available":sh_available,
                                                   "state":"READY" if sh_available else "DISABLED" if not gallery_shizuku.enabled() else "DEFERRED"}},
                "deferred_sources":[] if sh_available else (["android_data_shizuku"] if sh else [])}
    tools["gallery_source_status"]=(schema(),status)
    descriptions["gallery_source_status"]="Show indexed photo sources and whether each source is currently available."
    readonly.add("gallery_source_status")
