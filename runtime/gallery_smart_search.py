"""Unified local photo retrieval across metadata/OCR and semantic embeddings.
Author: @dvkrum
"""
from __future__ import annotations
from typing import Any

def install(g: dict[str,Any]) -> None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"]
    base_search=g["gallery_search"]
    semantic=tools["gallery_semantic_search"][1]
    def smart(args):
        query=str(args.get("query","")).strip()
        if not query: raise ValueError("query is required")
        limit=max(1,min(int(args.get("limit",60)),200)); merged=[]; seen=set()
        exact=base_search({"query":query,"limit":limit,"offset":0})
        for item in exact.get("items",[]):
            iid=int(item["id"])
            if iid in seen:continue
            seen.add(iid); merged.append({**item,"match":["metadata_or_ocr"],"retrieval_score":1.0})
        sem=semantic({"query":query,"limit":limit})
        if sem.get("ok"):
            for item in sem.get("items",[]):
                iid=int(item["id"]); score=float(item.get("semantic_score",0))
                if iid in seen:
                    for existing in merged:
                        if int(existing["id"])==iid:
                            existing["match"].append("semantic");existing["semantic_score"]=score;break
                    continue
                seen.add(iid); merged.append({**item,"match":["semantic"],"retrieval_score":score})
        return {"ok":True,"query":query,"count":min(len(merged),limit),"items":merged[:limit],"semantic_available":bool(sem.get("ok"))}
    tools["gallery_smart_search"]=(schema({"query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":200,"default":60}},["query"]),smart)
    descriptions["gallery_smart_search"]="Unified local retrieval across metadata, OCR text, and CLIP semantic image embeddings when available."
    readonly.add("gallery_smart_search")
