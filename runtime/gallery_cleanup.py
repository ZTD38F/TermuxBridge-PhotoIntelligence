"""Cleanup recommendation and planning layer.
Author: @dvkrum
Source files are never changed by this module.
"""
from __future__ import annotations
import json, time, uuid
from typing import Any

def install(g: dict[str, Any]) -> None:
    schema=g["_schema"]; tools=g["TOOLS"]; descriptions=g["DESCRIPTIONS"]; readonly=g["READ_ONLY"]; db=g["_db"]
    def ensure(c):
        c.execute("CREATE TABLE IF NOT EXISTS gallery_cleanup_plans(plan_id TEXT PRIMARY KEY,created_at REAL NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL)")
    def candidates(args):
        limit=max(1,min(int(args.get("limit",200)),1000)); items=[]
        # Initialize dependent schemas without mutating source files.
        if "gallery_visual_status" in tools: tools["gallery_visual_status"][1]({})
        exact_delete={}  # image_id -> keeper_id
        if "gallery_exact_duplicates" in tools:
            exact=tools["gallery_exact_duplicates"][1]({"limit":500})
            with db() as c:
                for group in exact.get("groups",[]):
                    ids=group["ids"]
                    q="SELECT id,path,mtime FROM images WHERE id IN (%s)" % ",".join("?"*len(ids))
                    rows0=c.execute(q,ids).fetchall()
                    def keep_score(row):
                        path=str(row["path"]).lower()
                        return (0 if "/cache/" in path or "/.cache/" in path else 1,
                                1 if "dcim/camera" in path else 0,float(row["mtime"]))
                    keeper=max(rows0,key=keep_score)["id"] if rows0 else ids[0]
                    for iid in ids:
                        if iid!=keeper: exact_delete[int(iid)]=int(keeper)
        with db() as c:
            rows=c.execute("""SELECT i.id,i.path,i.name,i.album,i.size,i.mtime,v.sharpness,v.brightness
                FROM images i LEFT JOIN gallery_visual_features v ON v.image_id=i.id AND v.size=i.size AND v.mtime=i.mtime
                ORDER BY i.size DESC LIMIT ?""",(max(limit*8,limit),)).fetchall()
        for r in rows:
            path=str(r["path"]).lower(); reasons=[]; confidence=0.0; keeper_id=None
            if int(r["id"]) in exact_delete:
                reasons.append("exact_duplicate");confidence=1.0;keeper_id=exact_delete[int(r["id"])]
            if "/cache/" in path or "/.cache/" in path: reasons.append("app_cache"); confidence=max(confidence,0.92)
            if int(r["size"])<=1024: reasons.append("tiny_artifact"); confidence=max(confidence,0.85)
            if r["sharpness"] is not None and float(r["sharpness"])<2.0: reasons.append("very_blurry"); confidence=max(confidence,0.70)
            if r["brightness"] is not None and float(r["brightness"])<8.0: reasons.append("nearly_black"); confidence=max(confidence,0.70)
            if reasons:
                item={"id":r["id"],"name":r["name"],"album":r["album"],"size":r["size"],"reasons":reasons,"confidence":round(confidence,2)}
                if keeper_id is not None:item["duplicate_of"]=keeper_id
                items.append(item)
                if len(items)>=limit: break
        return {"ok":True,"count":len(items),"items":items,"source_files_modified":False}
    def create_plan(args):
        ids=args.get("ids",[])
        if not isinstance(ids,list) or not 1<=len(ids)<=1000: raise ValueError("ids must contain 1..1000 image IDs")
        with db() as c:
            ensure(c); q="SELECT id,path,name,album,size,mtime FROM images WHERE id IN (%s)" % ",".join("?"*len(ids))
            rows=c.execute(q,[int(x) for x in ids]).fetchall(); plan_id=uuid.uuid4().hex
            payload={"ids":[int(r["id"]) for r in rows],"bytes":sum(int(r["size"]) for r in rows),"items":[{"id":r["id"],"name":r["name"],"album":r["album"],"size":r["size"]} for r in rows],"requires_user_approval":True,"source_files_modified":False}
            c.execute("INSERT INTO gallery_cleanup_plans(plan_id,created_at,payload,state) VALUES(?,?,?,?)",(plan_id,time.time(),json.dumps(payload,separators=(",",":")),"PROPOSED"))
        return {"ok":True,"plan_id":plan_id,**payload}
    def get_plan(args):
        with db() as c:
            ensure(c); row=c.execute("SELECT * FROM gallery_cleanup_plans WHERE plan_id=?",(str(args["plan_id"]),)).fetchone()
        if not row: raise ValueError("unknown cleanup plan")
        return {"ok":True,"plan_id":row["plan_id"],"created_at":row["created_at"],"state":row["state"],**json.loads(row["payload"])}
    tools["gallery_cleanup_candidates"]=(schema({"limit":{"type":"integer","minimum":1,"maximum":1000,"default":200}}),candidates)
    tools["gallery_cleanup_plan"]=(schema({"ids":{"type":"array","items":{"type":"integer","minimum":1},"minItems":1,"maxItems":1000}},["ids"]),create_plan)
    tools["gallery_cleanup_plan_get"]=(schema({"plan_id":{"type":"string"}},["plan_id"]),get_plan)
    descriptions.update({"gallery_cleanup_candidates":"Recommend likely cleanup candidates with reasons and confidence. No files are changed.","gallery_cleanup_plan":"Create a reviewable cleanup plan. No files are changed.","gallery_cleanup_plan_get":"Read a previously created cleanup plan."})
    readonly.update({"gallery_cleanup_candidates","gallery_cleanup_plan_get"})
