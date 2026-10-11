"""Cleanup recommendation, quarantine, undo and purge state machine.
Author: @dvkrum
"""
from __future__ import annotations
import json,os,shutil,time,uuid
from pathlib import Path
from typing import Any

def _atomic(path:Path,payload:dict):
    tmp=path.with_name("."+path.name+".tmp");tmp.write_text(json.dumps(payload,separators=(",",":"))+"\n");tmp.chmod(0o600);os.replace(tmp,path)

def _queue_reconcile(state:Path,reason:str)->bool:
    try:
        _atomic(state/"source_dirty.json",{"dirty":True,"timestamp":time.time(),"reason":reason,"path":""})
        return True
    except OSError:
        return False

def install(g:dict[str,Any])->None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"]
    root:Path=g["SHARED_ROOT"].resolve();state_dir:Path=g["STATE_DIR"];quarantine_dir=root/".photo-intelligence-quarantine"
    destructive=g.setdefault("DESTRUCTIVE",set())
    def ensure(c):
        c.execute("CREATE TABLE IF NOT EXISTS gallery_cleanup_plans(plan_id TEXT PRIMARY KEY,created_at REAL NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL)")
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_cleanup_actions(
          action_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,image_id INTEGER NOT NULL,
          original_path TEXT NOT NULL,quarantine_path TEXT NOT NULL,size INTEGER NOT NULL,mtime REAL NOT NULL,
          state TEXT NOT NULL,quarantined_at REAL NOT NULL,restored_at REAL,purged_at REAL,
          FOREIGN KEY(plan_id) REFERENCES gallery_cleanup_plans(plan_id) ON DELETE CASCADE)""")
        c.execute("CREATE INDEX IF NOT EXISTS cleanup_actions_plan_idx ON gallery_cleanup_actions(plan_id,state)")
    def _load(plan_id):
        with db() as c:
            ensure(c);row=c.execute("SELECT * FROM gallery_cleanup_plans WHERE plan_id=?",(plan_id,)).fetchone()
        if not row:raise ValueError("unknown cleanup plan")
        return row,json.loads(row["payload"])
    def _confirm(args):
        pid=str(args.get("plan_id",""));
        if args.get("approved") is not True:raise PermissionError("explicit approved=true required")
        if str(args.get("confirm_plan_id",""))!=pid:raise PermissionError("confirm_plan_id must exactly match plan_id")
        return pid
    def _safe_local(rel:str)->Path:
        if rel.startswith("shizuku://"):raise PermissionError("Android/data quarantine is intentionally unsupported")
        raw=Path(rel)
        if raw.is_absolute() or ".." in raw.parts:raise ValueError("unsafe indexed path")
        path=(root/raw).resolve()
        if not path.is_relative_to(root):raise ValueError("path escapes shared storage")
        if path==quarantine_dir or path.is_relative_to(quarantine_dir):raise ValueError("path already inside quarantine")
        return path
    def candidates(args):
        limit=max(1,min(int(args.get("limit",200)),1000));items=[]
        if "gallery_visual_status" in tools:tools["gallery_visual_status"][1]({})
        exact_delete={}
        if "gallery_exact_duplicates" in tools:
            exact=tools["gallery_exact_duplicates"][1]({"limit":500})
            with db() as c:
                for group in exact.get("groups",[]):
                    ids=group["ids"];q="SELECT id,path,mtime FROM images WHERE id IN (%s)" % ",".join("?"*len(ids));rows0=c.execute(q,ids).fetchall()
                    def keep_score(row):
                        path=str(row["path"]).lower();return (0 if "/cache/" in path or "/.cache/" in path else 1,1 if "dcim/camera" in path else 0,float(row["mtime"]))
                    keeper=max(rows0,key=keep_score)["id"] if rows0 else ids[0]
                    for iid in ids:
                        if iid!=keeper:exact_delete[int(iid)]=int(keeper)
        with db() as c:
            try:
                rows=c.execute("""SELECT i.id,i.path,i.name,i.album,i.size,i.mtime,v.sharpness,v.brightness
                    FROM images i LEFT JOIN gallery_visual_features v ON v.image_id=i.id AND v.size=i.size AND v.mtime=i.mtime
                    ORDER BY i.size DESC LIMIT ?""",(max(limit*10,limit),)).fetchall()
            except Exception:
                rows=c.execute("SELECT id,path,name,album,size,mtime,NULL sharpness,NULL brightness FROM images ORDER BY size DESC LIMIT ?",(max(limit*10,limit),)).fetchall()
        now=time.time()
        for r in rows:
            path=str(r["path"]).lower();name=str(r["name"]).lower();reasons=[];confidence=0.0;keeper_id=None
            if int(r["id"]) in exact_delete:reasons.append("exact_duplicate");confidence=1.0;keeper_id=exact_delete[int(r["id"])]
            if "/cache/" in path or "/.cache/" in path:reasons.append("app_cache");confidence=max(confidence,.92)
            if int(r["size"])<=1024:reasons.append("tiny_artifact");confidence=max(confidence,.85)
            if r["sharpness"] is not None and float(r["sharpness"])<2.0:reasons.append("very_blurry");confidence=max(confidence,.70)
            if r["brightness"] is not None and float(r["brightness"])<8.0:reasons.append("nearly_black");confidence=max(confidence,.70)
            if "screenshot" in name and now-float(r["mtime"] or now)>90*86400:reasons.append("old_screenshot");confidence=max(confidence,.65)
            if reasons:
                entry={"id":r["id"],"name":r["name"],"album":r["album"],"size":r["size"],"source":"android_data" if str(r["path"]).startswith("shizuku://") else "local","reasons":reasons,"confidence":round(confidence,2),"quarantine_supported":not str(r["path"]).startswith("shizuku://")}
                if keeper_id is not None:entry["duplicate_of"]=keeper_id
                items.append(entry)
                if len(items)>=limit:break
        return {"ok":True,"count":len(items),"items":items,"source_files_modified":False}
    def create_plan(args):
        ids=list(dict.fromkeys(int(x) for x in args.get("ids",[])));undo_hours=max(1,min(int(args.get("undo_hours",72)),720))
        if not 1<=len(ids)<=1000:raise ValueError("ids must contain 1..1000 unique image IDs")
        with db() as c:
            ensure(c);q="SELECT id,path,name,album,size,mtime FROM images WHERE id IN (%s)" % ",".join("?"*len(ids));rows=c.execute(q,ids).fetchall()
            if len(rows)!=len(ids):raise ValueError("one or more image IDs are unknown")
            plan_id=uuid.uuid4().hex;items=[{"id":int(r["id"]),"path":r["path"],"name":r["name"],"album":r["album"],"size":int(r["size"]),"mtime":float(r["mtime"]),"source":"android_data" if str(r["path"]).startswith("shizuku://") else "local"} for r in rows]
            payload={"ids":ids,"bytes":sum(x["size"] for x in items),"items":items,"undo_hours":undo_hours,"requires_user_approval":True,"source_files_modified":False}
            c.execute("INSERT INTO gallery_cleanup_plans(plan_id,created_at,payload,state) VALUES(?,?,?,?)",(plan_id,time.time(),json.dumps(payload,separators=(",",":")),"PROPOSED"))
        return {"ok":True,"plan_id":plan_id,**payload}
    def get_plan(args):
        row,payload=_load(str(args["plan_id"]));
        with db() as c:
            ensure(c);actions=[dict(x) for x in c.execute("SELECT action_id,image_id,original_path,quarantine_path,size,state,quarantined_at,restored_at,purged_at FROM gallery_cleanup_actions WHERE plan_id=? ORDER BY quarantined_at,image_id",(row["plan_id"],)).fetchall()]
        return {"ok":True,"plan_id":row["plan_id"],"created_at":row["created_at"],"state":row["state"],**payload,"actions":actions}
    def quarantine(args):
        plan_id=_confirm(args);row,payload=_load(plan_id)
        if row["state"]=="QUARANTINED":return get_plan({"plan_id":plan_id})
        if row["state"]!="PROPOSED":raise ValueError("plan is not in PROPOSED state")
        pre=[]
        with db() as c:
            ensure(c)
            for expected in payload["items"]:
                if expected.get("source")=="android_data":raise PermissionError("plans containing Android/data cannot be quarantined")
                current=c.execute("SELECT * FROM images WHERE id=?",(int(expected["id"]),)).fetchone()
                if current is None:raise ValueError("planned image no longer indexed")
                if str(current["path"])!=str(expected["path"]) or int(current["size"])!=int(expected["size"]) or abs(float(current["mtime"])-float(expected["mtime"]))>0.00001:raise ValueError("planned image changed; create a new cleanup plan")
                g["_validate_row"](current);src=_safe_local(str(current["path"]));rel=Path(str(current["path"]));dst=(quarantine_dir/plan_id/rel).resolve()
                if not dst.is_relative_to((quarantine_dir/plan_id).resolve()):raise ValueError("unsafe quarantine destination")
                if dst.exists():raise FileExistsError("quarantine destination already exists")
                pre.append((current,src,dst))
        if quarantine_dir.is_symlink():raise ValueError("quarantine root must not be a symlink")
        if not quarantine_dir.resolve().is_relative_to(root):raise ValueError("quarantine root escapes shared storage")
        moved=[]
        try:
            for current,src,dst in pre:
                dst.parent.mkdir(parents=True,exist_ok=True);os.replace(src,dst);moved.append((current,src,dst))
            now=time.time();purge_after=now+int(payload.get("undo_hours",72))*3600
            payload.update({"quarantined_at":now,"purge_after":purge_after,"bytes_quarantined":sum(int(x[0]["size"]) for x in moved),"source_files_modified":True})
            with db() as c:
                ensure(c);c.execute("BEGIN IMMEDIATE")
                for current,src,dst in moved:
                    image_id=int(current["id"])
                    c.execute("INSERT INTO gallery_cleanup_actions(action_id,plan_id,image_id,original_path,quarantine_path,size,mtime,state,quarantined_at) VALUES(?,?,?,?,?,?,?,?,?)",(uuid.uuid4().hex,plan_id,image_id,str(current["path"]),str(dst.relative_to(root)),int(current["size"]),float(current["mtime"]),"QUARANTINED",now))
                    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
                    if "gallery_ocr_fts" in tables:c.execute("DELETE FROM gallery_ocr_fts WHERE rowid=?",(image_id,))
                    if "gallery_ocr_index" in tables:c.execute("DELETE FROM gallery_ocr_index WHERE image_id=?",(image_id,))
                    c.execute("DELETE FROM images WHERE id=?",(image_id,))
                c.execute("UPDATE gallery_cleanup_plans SET state='QUARANTINED',payload=? WHERE plan_id=?",(json.dumps(payload,separators=(",",":")),plan_id));c.commit()
        except Exception:
            for current,src,dst in reversed(moved):
                try:
                    if dst.exists() and not src.exists():src.parent.mkdir(parents=True,exist_ok=True);os.replace(dst,src)
                except Exception:pass
            raise
        queued=_queue_reconcile(state_dir,"cleanup_quarantine");return {"ok":True,"plan_id":plan_id,"state":"QUARANTINED","count":len(moved),"bytes":payload["bytes_quarantined"],"purge_after":payload["purge_after"],"reversible":True,"source_files_modified":True,"reconcile_queued":queued}
    def restore(args):
        plan_id=_confirm(args);row,payload=_load(plan_id)
        if row["state"]=="RESTORED":return get_plan({"plan_id":plan_id})
        if row["state"]!="QUARANTINED":raise ValueError("only QUARANTINED plans can be restored")
        with db() as c:ensure(c);actions=c.execute("SELECT * FROM gallery_cleanup_actions WHERE plan_id=? AND state='QUARANTINED' ORDER BY quarantined_at DESC",(plan_id,)).fetchall()
        pre=[]
        for action in actions:
            src=(root/str(action["original_path"])).resolve();q=(root/str(action["quarantine_path"])).resolve()
            if not src.is_relative_to(root) or not q.is_relative_to(quarantine_dir):raise ValueError("unsafe restore path")
            if src.exists():raise FileExistsError("original path already occupied")
            if not q.is_file():raise FileNotFoundError("quarantined file is missing")
            pre.append((action,q,src))
        restored=[]
        try:
            for action,q,src in pre:src.parent.mkdir(parents=True,exist_ok=True);os.replace(q,src);restored.append((action,q,src))
            now=time.time()
            with db() as c:
                ensure(c);c.execute("BEGIN IMMEDIATE");c.execute("UPDATE gallery_cleanup_actions SET state='RESTORED',restored_at=? WHERE plan_id=? AND state='QUARANTINED'",(now,plan_id));c.execute("UPDATE gallery_cleanup_plans SET state='RESTORED' WHERE plan_id=?",(plan_id,));c.commit()
        except Exception:
            for action,q,src in reversed(restored):
                try:
                    if src.exists() and not q.exists():q.parent.mkdir(parents=True,exist_ok=True);os.replace(src,q)
                except Exception:pass
            raise
        plan_root=(quarantine_dir/plan_id).resolve()
        if plan_root.is_relative_to(quarantine_dir):shutil.rmtree(plan_root,ignore_errors=True)
        queued=_queue_reconcile(state_dir,"cleanup_restore");return {"ok":True,"plan_id":plan_id,"state":"RESTORED","count":len(restored),"source_files_modified":True,"reconcile_queued":queued}
    def purge(args):
        plan_id=_confirm(args);row,payload=_load(plan_id)
        if row["state"]=="PURGED":return get_plan({"plan_id":plan_id})
        if row["state"] not in {"QUARANTINED","PURGE_PARTIAL"}:raise ValueError("only QUARANTINED/PURGE_PARTIAL plans can be purged")
        purge_after=float(payload.get("purge_after",0))
        if time.time()<purge_after:raise PermissionError("undo window is still active")
        with db() as c:ensure(c);actions=c.execute("SELECT * FROM gallery_cleanup_actions WHERE plan_id=? AND state='QUARANTINED'",(plan_id,)).fetchall()
        purged=0;bytes_=0;failures=[]
        for action in actions:
            q=(root/str(action["quarantine_path"])).resolve()
            if not q.is_relative_to(quarantine_dir):failures.append({"image_id":action["image_id"],"error":"unsafe_path"});continue
            try:
                if q.exists():q.unlink()
                now=time.time();bytes_+=int(action["size"]);purged+=1
                with db() as c:ensure(c);c.execute("UPDATE gallery_cleanup_actions SET state='PURGED',purged_at=? WHERE action_id=?",(now,action["action_id"]));c.commit()
            except Exception as exc:failures.append({"image_id":action["image_id"],"error":type(exc).__name__})
        state="PURGED" if not failures else "PURGE_PARTIAL"
        with db() as c:ensure(c);c.execute("UPDATE gallery_cleanup_plans SET state=? WHERE plan_id=?",(state,plan_id));c.commit()
        plan_root=(quarantine_dir/plan_id).resolve()
        if state=="PURGED" and plan_root.is_relative_to(quarantine_dir):shutil.rmtree(plan_root,ignore_errors=True)
        return {"ok":not failures,"plan_id":plan_id,"state":state,"purged":purged,"bytes":bytes_,"failures":failures,"irreversible":True,"source_files_modified":True}
    def quarantine_status(_):
        with db() as c:
            ensure(c);rows=c.execute("SELECT state,COUNT(*) n FROM gallery_cleanup_plans GROUP BY state").fetchall();q=c.execute("SELECT COALESCE(SUM(size),0) bytes,COUNT(*) n FROM gallery_cleanup_actions WHERE state='QUARANTINED'").fetchone()
        return {"ok":True,"plans":{r["state"]:r["n"] for r in rows},"quarantined_files":int(q["n"]),"quarantined_bytes":int(q["bytes"]),"quarantine_root":str(quarantine_dir.relative_to(root)),"android_data_mutation_supported":False}

    tools["gallery_cleanup_candidates"]=(schema({"limit":{"type":"integer","minimum":1,"maximum":1000,"default":200}}),candidates)
    tools["gallery_cleanup_plan"]=(schema({"ids":{"type":"array","items":{"type":"integer","minimum":1},"minItems":1,"maxItems":1000},"undo_hours":{"type":"integer","minimum":1,"maximum":720,"default":72}},["ids"]),create_plan)
    tools["gallery_cleanup_plan_get"]=(schema({"plan_id":{"type":"string"}},["plan_id"]),get_plan)
    confirm={"plan_id":{"type":"string"},"approved":{"type":"boolean"},"confirm_plan_id":{"type":"string"}}
    tools["gallery_cleanup_quarantine"]=(schema(confirm,["plan_id","approved","confirm_plan_id"]),quarantine)
    tools["gallery_cleanup_restore"]=(schema(confirm,["plan_id","approved","confirm_plan_id"]),restore)
    tools["gallery_cleanup_purge"]=(schema(confirm,["plan_id","approved","confirm_plan_id"]),purge)
    tools["gallery_quarantine_status"]=(schema(),quarantine_status)
    descriptions.update({
      "gallery_cleanup_candidates":"Recommend likely cleanup candidates with reasons/confidence. No files are changed.",
      "gallery_cleanup_plan":"Create an immutable reviewable cleanup plan with an undo window. No files are changed.",
      "gallery_cleanup_plan_get":"Read cleanup plan state and quarantine actions.",
      "gallery_cleanup_quarantine":"After explicit confirmation, move planned local shared-storage images into reversible quarantine. Android/data is refused.",
      "gallery_cleanup_restore":"After explicit confirmation, restore a quarantined cleanup plan to original paths.",
      "gallery_cleanup_purge":"Irreversibly delete files already in quarantine after the undo window and explicit confirmation.",
      "gallery_quarantine_status":"Show cleanup/quarantine counts and bytes without changing files."
    })
    readonly.update({"gallery_cleanup_candidates","gallery_cleanup_plan_get","gallery_quarantine_status"});destructive.update({"gallery_cleanup_quarantine","gallery_cleanup_restore","gallery_cleanup_purge"})
