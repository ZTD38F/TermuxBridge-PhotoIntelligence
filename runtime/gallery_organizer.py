"""Virtual organization layer for Photo Intelligence.
Author: @dvkrum
"""
from __future__ import annotations
from typing import Any

def install(g: dict[str, Any]) -> None:
    schema=g["_schema"]; tools=g["TOOLS"]; descriptions=g["DESCRIPTIONS"]; readonly=g["READ_ONLY"]; db=g["_db"]
    rules=(
        ("Screenshots",("screenshot",)),
        ("Camera",("dcim/camera",)),
        ("Downloads",("download",)),
        ("Messaging",("telegram","whatsapp","ayugram","messenger")),
        ("Documents",("document","scan","receipt","invoice")),
        ("App cache",("/cache/","/.cache/")),
    )
    def categories(_):
        out=[]
        with db() as c:
            total=c.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            for name,terms in rules:
                cond=" OR ".join(["lower(path) LIKE ?" for _ in terms]); params=[f"%{t}%" for t in terms]
                count=c.execute(f"SELECT COUNT(*) FROM images WHERE {cond}",params).fetchone()[0]
                out.append({"name":name,"count":count,"virtual":True})
        return {"ok":True,"total":total,"categories":out,"source_files_moved":False}
    def category_items(args):
        name=str(args["category"]); rule=next((t for n,t in rules if n==name),None)
        if rule is None: raise ValueError("unknown category")
        limit=max(1,min(int(args.get("limit",100)),500)); offset=max(0,int(args.get("offset",0)))
        cond=" OR ".join(["lower(path) LIKE ?" for _ in rule]); params=[f"%{t}%" for t in rule]
        with db() as c:
            total=c.execute(f"SELECT COUNT(*) FROM images WHERE {cond}",params).fetchone()[0]
            rows=c.execute(f"SELECT * FROM images WHERE {cond} ORDER BY mtime DESC LIMIT ? OFFSET ?",[*params,limit,offset]).fetchall()
        return {"ok":True,"category":name,"total":total,"items":[g["_item"](r) for r in rows],"next_offset":offset+len(rows) if offset+len(rows)<total else None}
    tools["gallery_categories"]=(schema(),categories)
    tools["gallery_category_items"]=(schema({"category":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":500,"default":100},"offset":{"type":"integer","minimum":0,"default":0}},["category"]),category_items)
    descriptions.update({"gallery_categories":"List virtual photo categories without moving files.","gallery_category_items":"List photos in a virtual category without changing file paths."})
    readonly.update({"gallery_categories","gallery_category_items"})
