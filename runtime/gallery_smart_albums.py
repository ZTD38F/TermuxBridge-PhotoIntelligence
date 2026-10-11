"""Virtual tags and user-defined smart albums.
Author: @dvkrum
No source file is moved, renamed or modified.
"""
from __future__ import annotations
import json,re,time,uuid
from typing import Any

BUILTINS={
 "builtin:recent-30d":("Recent 30 days",{"date_recent_days":30}),
 "builtin:screenshots":("Screenshots",{"path_contains":"screenshot"}),
 "builtin:camera":("Camera",{"path_contains":"dcim/camera"}),
 "builtin:gps":("With GPS",{"has_gps":True}),
 "builtin:large":("Large files",{"min_size":10*1024*1024}),
}
ALLOWED={"path_contains","name_contains","album_contains","date_recent_days","date_from","date_to","min_size","max_size","min_width","max_width","min_height","max_height","make","model","has_gps","orientation","source","mime_prefix","tags_all"}

def install(g:dict[str,Any])->None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"]
    def ensure(c):
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_smart_albums(
          album_id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE UNIQUE,created_at REAL NOT NULL,updated_at REAL NOT NULL,criteria TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_tags(
          image_id INTEGER NOT NULL,tag TEXT NOT NULL COLLATE NOCASE,created_at REAL NOT NULL,
          PRIMARY KEY(image_id,tag),FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
        c.execute("CREATE INDEX IF NOT EXISTS gallery_tags_tag_idx ON gallery_tags(tag)")
    def normalize_tag(value):
        tag=re.sub(r"\s+"," ",str(value).strip())
        if not 1<=len(tag)<=60 or any(ord(c)<32 for c in tag):raise ValueError("tag must be 1..60 printable characters")
        return tag
    def validate_criteria(raw):
        if not isinstance(raw,dict):raise ValueError("criteria object required")
        unknown=set(raw)-ALLOWED
        if unknown:raise ValueError("unknown criteria: "+",".join(sorted(unknown)))
        c=dict(raw)
        for k in ("path_contains","name_contains","album_contains","make","model","mime_prefix"):
            if k in c:
                c[k]=str(c[k]).strip()
                if not c[k] or len(c[k])>160:raise ValueError(k+" is invalid")
        for k in ("date_recent_days","min_size","max_size","min_width","max_width","min_height","max_height"):
            if k in c:c[k]=int(c[k]); 
        if "date_recent_days" in c and not 1<=c["date_recent_days"]<=36500:raise ValueError("date_recent_days out of range")
        for k in ("date_from","date_to"):
            if k in c:c[k]=float(c[k])
        if "has_gps" in c and not isinstance(c["has_gps"],bool):raise ValueError("has_gps must be boolean")
        if c.get("orientation") not in (None,"portrait","landscape","square"):raise ValueError("invalid orientation")
        if c.get("source") not in (None,"local","android_data"):raise ValueError("invalid source")
        if "tags_all" in c:
            if not isinstance(c["tags_all"],list) or len(c["tags_all"])>16:raise ValueError("tags_all must be a list of up to 16 tags")
            c["tags_all"]=[normalize_tag(x) for x in c["tags_all"]]
        if not c:raise ValueError("at least one criterion required")
        return c
    def sql_for(criteria):
        where=[];params=[];join_meta=False
        def like(key,col):
            if criteria.get(key):where.append(f"lower({col}) LIKE ?");params.append("%"+str(criteria[key]).lower()+"%")
        like("path_contains","i.path");like("name_contains","i.name");like("album_contains","i.album")
        if "date_recent_days" in criteria:where.append("i.mtime>=?");params.append(time.time()-int(criteria["date_recent_days"])*86400)
        if "date_from" in criteria:where.append("COALESCE(m.taken_epoch,i.mtime)>=?");params.append(float(criteria["date_from"]));join_meta=True
        if "date_to" in criteria:where.append("COALESCE(m.taken_epoch,i.mtime)<=?");params.append(float(criteria["date_to"]));join_meta=True
        for key,op,col in (("min_size",">=","i.size"),("max_size","<=","i.size"),("min_width",">=","m.width"),("max_width","<=","m.width"),("min_height",">=","m.height"),("max_height","<=","m.height")):
            if key in criteria:where.append(f"{col}{op}?");params.append(int(criteria[key]));join_meta=join_meta or col.startswith("m.")
        for key,col in (("make","m.camera_make"),("model","m.camera_model")):
            if criteria.get(key):where.append(f"lower({col}) LIKE ?");params.append("%"+str(criteria[key]).lower()+"%");join_meta=True
        if "has_gps" in criteria:
            where.append("m.gps_lat IS NOT NULL AND m.gps_lon IS NOT NULL" if criteria["has_gps"] else "(m.gps_lat IS NULL OR m.gps_lon IS NULL)");join_meta=True
        if criteria.get("orientation"):
            join_meta=True;o=criteria["orientation"]
            where.append({"portrait":"m.height>m.width","landscape":"m.width>m.height","square":"m.width=m.height"}[o])
        if criteria.get("source")=="local":where.append("i.path NOT LIKE 'shizuku://%'")
        if criteria.get("source")=="android_data":where.append("i.path LIKE 'shizuku://%'")
        if criteria.get("mime_prefix"):where.append("lower(COALESCE(i.mime,'')) LIKE ?");params.append(str(criteria["mime_prefix"]).lower()+"%")
        for tag in criteria.get("tags_all",[]):
            where.append("EXISTS(SELECT 1 FROM gallery_tags t WHERE t.image_id=i.id AND lower(t.tag)=lower(?))");params.append(tag)
        join=" LEFT JOIN gallery_metadata_index m ON m.image_id=i.id AND m.size=i.size AND m.mtime=i.mtime" if join_meta else ""
        return join,(" AND ".join(where) if where else "1=1"),params
    def _album(album_id):
        if album_id in BUILTINS:return {"album_id":album_id,"name":BUILTINS[album_id][0],"criteria":BUILTINS[album_id][1],"builtin":True}
        with db() as c:
            ensure(c);row=c.execute("SELECT * FROM gallery_smart_albums WHERE album_id=?",(album_id,)).fetchone()
        if not row:raise ValueError("unknown smart album")
        return {"album_id":row["album_id"],"name":row["name"],"criteria":json.loads(row["criteria"]),"builtin":False,"created_at":row["created_at"],"updated_at":row["updated_at"]}
    def count(criteria):
        join,where,params=sql_for(criteria)
        with db() as c:ensure(c);return c.execute(f"SELECT COUNT(*) FROM images i{join} WHERE {where}",params).fetchone()[0]
    def albums(_):
        out=[]
        for aid,(name,c) in BUILTINS.items():out.append({"album_id":aid,"name":name,"criteria":c,"builtin":True,"count":count(c)})
        with db() as c:ensure(c);rows=c.execute("SELECT * FROM gallery_smart_albums ORDER BY lower(name)").fetchall()
        for r in rows:
            c=json.loads(r["criteria"]);out.append({"album_id":r["album_id"],"name":r["name"],"criteria":c,"builtin":False,"count":count(c),"created_at":r["created_at"],"updated_at":r["updated_at"]})
        return {"ok":True,"albums":out,"source_files_moved":False}
    def create(args):
        name=re.sub(r"\s+"," ",str(args["name"]).strip())
        if not 1<=len(name)<=80:raise ValueError("name must be 1..80 characters")
        criteria=validate_criteria(args["criteria"]);aid=uuid.uuid4().hex;now=time.time()
        with db() as c:
            ensure(c);c.execute("INSERT INTO gallery_smart_albums(album_id,name,created_at,updated_at,criteria) VALUES(?,?,?,?,?)",(aid,name,now,now,json.dumps(criteria,separators=(",",":"))))
        return {"ok":True,"album_id":aid,"name":name,"criteria":criteria,"count":count(criteria),"source_files_modified":False}
    def delete(args):
        aid=str(args["album_id"])
        if aid.startswith("builtin:"):raise PermissionError("built-in smart albums cannot be deleted")
        if str(args.get("confirm_album_id",""))!=aid:raise PermissionError("confirm_album_id must exactly match album_id")
        with db() as c:
            ensure(c);n=c.execute("DELETE FROM gallery_smart_albums WHERE album_id=?",(aid,)).rowcount
        if not n:raise ValueError("unknown smart album")
        return {"ok":True,"album_id":aid,"deleted":True,"source_files_modified":False}
    def items(args):
        a=_album(str(args["album_id"]));limit=max(1,min(int(args.get("limit",100)),500));offset=max(0,int(args.get("offset",0)));join,where,params=sql_for(a["criteria"])
        with db() as c:
            ensure(c);total=c.execute(f"SELECT COUNT(*) FROM images i{join} WHERE {where}",params).fetchone()[0]
            rows=c.execute(f"SELECT i.* FROM images i{join} WHERE {where} ORDER BY i.mtime DESC LIMIT ? OFFSET ?",[*params,limit,offset]).fetchall()
        return {"ok":True,"album":a,"total":total,"count":len(rows),"offset":offset,"next_offset":offset+len(rows) if offset+len(rows)<total else None,"items":[g["_item"](r) for r in rows],"source_files_moved":False}
    def tag_add(args):
        tag=normalize_tag(args["tag"]);ids=list(dict.fromkeys(int(x) for x in args["ids"]))
        if not 1<=len(ids)<=200:raise ValueError("ids must contain 1..200 items")
        with db() as c:
            ensure(c);known={r[0] for r in c.execute("SELECT id FROM images WHERE id IN (%s)"%",".join("?"*len(ids)),ids)}
            if len(known)!=len(ids):raise ValueError("one or more image IDs are unknown")
            now=time.time();c.executemany("INSERT OR IGNORE INTO gallery_tags(image_id,tag,created_at) VALUES(?,?,?)",[(i,tag,now) for i in ids])
        return {"ok":True,"tag":tag,"ids":ids,"source_files_modified":False}
    def tag_remove(args):
        tag=normalize_tag(args["tag"]);ids=list(dict.fromkeys(int(x) for x in args["ids"]))
        if not 1<=len(ids)<=200:raise ValueError("ids must contain 1..200 items")
        with db() as c:
            ensure(c);q="DELETE FROM gallery_tags WHERE tag=? AND image_id IN (%s)"%",".join("?"*len(ids));n=c.execute(q,[tag,*ids]).rowcount
        return {"ok":True,"tag":tag,"removed":n,"source_files_modified":False}
    def tags(_):
        with db() as c:ensure(c);rows=c.execute("SELECT tag,COUNT(*) n FROM gallery_tags GROUP BY lower(tag) ORDER BY n DESC,lower(tag)").fetchall()
        return {"ok":True,"tags":[{"tag":r["tag"],"count":r["n"]} for r in rows]}
    def tag_items(args):
        tag=normalize_tag(args["tag"]);limit=max(1,min(int(args.get("limit",100)),500));offset=max(0,int(args.get("offset",0)))
        with db() as c:
            ensure(c);total=c.execute("SELECT COUNT(*) FROM gallery_tags WHERE lower(tag)=lower(?)",(tag,)).fetchone()[0];rows=c.execute("""SELECT i.* FROM images i JOIN gallery_tags t ON t.image_id=i.id WHERE lower(t.tag)=lower(?) ORDER BY i.mtime DESC LIMIT ? OFFSET ?""",(tag,limit,offset)).fetchall()
        return {"ok":True,"tag":tag,"total":total,"items":[g["_item"](r) for r in rows],"next_offset":offset+len(rows) if offset+len(rows)<total else None}
    tools["gallery_smart_albums"]=(schema(),albums)
    tools["gallery_smart_album_create"]=(schema({"name":{"type":"string","minLength":1,"maxLength":80},"criteria":{"type":"object","additionalProperties":True}},["name","criteria"]),create)
    tools["gallery_smart_album_delete"]=(schema({"album_id":{"type":"string"},"confirm_album_id":{"type":"string"}},["album_id","confirm_album_id"]),delete)
    tools["gallery_smart_album_items"]=(schema({"album_id":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":500,"default":100},"offset":{"type":"integer","minimum":0,"default":0}},["album_id"]),items)
    tools["gallery_tag_add"]=(schema({"ids":{"type":"array","items":{"type":"integer","minimum":1},"minItems":1,"maxItems":200},"tag":{"type":"string","minLength":1,"maxLength":60}},["ids","tag"]),tag_add)
    tools["gallery_tag_remove"]=(schema({"ids":{"type":"array","items":{"type":"integer","minimum":1},"minItems":1,"maxItems":200},"tag":{"type":"string","minLength":1,"maxLength":60}},["ids","tag"]),tag_remove)
    tools["gallery_tags"]=(schema(),tags)
    tools["gallery_tag_items"]=(schema({"tag":{"type":"string","minLength":1,"maxLength":60},"limit":{"type":"integer","minimum":1,"maximum":500,"default":100},"offset":{"type":"integer","minimum":0,"default":0}},["tag"]),tag_items)
    descriptions.update({"gallery_smart_albums":"List built-in and custom virtual smart albums with live counts.","gallery_smart_album_create":"Create a virtual smart album rule. Source files are not moved.","gallery_smart_album_delete":"Delete only a custom smart-album definition after exact ID confirmation.","gallery_smart_album_items":"List photos currently matching a smart album rule.","gallery_tag_add":"Add local virtual tags to indexed photos without touching source files.","gallery_tag_remove":"Remove local virtual tags without touching source files.","gallery_tags":"List virtual tags and counts.","gallery_tag_items":"List photos carrying a virtual tag."})
    readonly.update({"gallery_smart_albums","gallery_smart_album_items","gallery_tags","gallery_tag_items"})
