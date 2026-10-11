"""Persistent exact and near-duplicate analysis.
Author: @dvkrum
"""
from __future__ import annotations
import hashlib,time
from collections import defaultdict
from typing import Any
import gallery_shizuku

def install(g: dict[str,Any]) -> None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"]
    def ensure(c):
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_duplicate_hash(
          image_id INTEGER PRIMARY KEY,size INTEGER NOT NULL,mtime REAL NOT NULL,processed_at REAL NOT NULL,
          sha256 TEXT,error TEXT,FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
        c.execute("CREATE INDEX IF NOT EXISTS gallery_duplicate_sha_idx ON gallery_duplicate_hash(size,sha256)")
    def candidate_count(c):
        return c.execute("SELECT COUNT(*) FROM images WHERE size IN (SELECT size FROM images GROUP BY size HAVING COUNT(*)>1)").fetchone()[0]
    def status(_):
        with db() as c:
            ensure(c); cand=candidate_count(c)
            sh_cand=c.execute("""SELECT COUNT(*) FROM images WHERE path LIKE 'shizuku://%' AND size IN
              (SELECT size FROM images GROUP BY size HAVING COUNT(*)>1)""").fetchone()[0]
            done=c.execute("""SELECT COUNT(*) FROM gallery_duplicate_hash h JOIN images i ON i.id=h.image_id
              WHERE h.size=i.size AND h.mtime=i.mtime AND i.size IN (SELECT size FROM images GROUP BY size HAVING COUNT(*)>1)""").fetchone()[0]
            sh_done=c.execute("""SELECT COUNT(*) FROM gallery_duplicate_hash h JOIN images i ON i.id=h.image_id
              WHERE h.size=i.size AND h.mtime=i.mtime AND i.path LIKE 'shizuku://%' AND
              i.size IN (SELECT size FROM images GROUP BY size HAVING COUNT(*)>1)""").fetchone()[0]
        local_cand=cand-sh_cand; local_done=done-sh_done; sh_available=gallery_shizuku.available_cached(ttl=30.0)
        return {"ok":True,"candidate_files":cand,"indexed":done,"remaining":max(0,cand-done),"percent":round(done*100/cand,2) if cand else 100.0,
                "local":{"total":local_cand,"indexed":local_done,"remaining":max(0,local_cand-local_done)},
                "android_data":{"total":sh_cand,"indexed":sh_done,"remaining":max(0,sh_cand-sh_done),"available":sh_available}}
    def index(args):
        limit=max(1,min(int(args.get("limit",64)),512));max_bytes=max(1,int(args.get("max_megabytes",256)))*1048576; deferred=set()
        shizuku_available=gallery_shizuku.available_cached(ttl=30.0)
        with db() as c:
            ensure(c)
            sql="""SELECT i.* FROM images i LEFT JOIN gallery_duplicate_hash h ON h.image_id=i.id
              WHERE i.size IN (SELECT size FROM images GROUP BY size HAVING COUNT(*)>1)
              AND (h.image_id IS NULL OR h.size<>i.size OR h.mtime<>i.mtime)"""
            params=[]
            if not shizuku_available:
                sql += " AND i.path NOT LIKE 'shizuku://%'"; deferred.add("android_data_shizuku")
            sql += " ORDER BY i.size ASC,i.id ASC LIMIT ?"; params.append(limit)
            rows=c.execute(sql,params).fetchall()
        ok=[];errors=[];used=0
        for row in rows:
            if used+int(row["size"])>max_bytes:break
            if "android_data_shizuku" in deferred and gallery_shizuku.is_virtual(str(row["path"])): continue
            try:
                h=hashlib.sha256()
                with g["_materialize_row"](row) as p:
                    with p.open("rb") as f:
                        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
                digest=h.hexdigest();g["_validate_row"](row);used+=int(row["size"])
                with db() as c:
                    ensure(c);c.execute("""INSERT INTO gallery_duplicate_hash(image_id,size,mtime,processed_at,sha256,error)
                      VALUES(?,?,?,?,?,NULL) ON CONFLICT(image_id) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,
                      processed_at=excluded.processed_at,sha256=excluded.sha256,error=NULL""",(row["id"],row["size"],row["mtime"],time.time(),digest))
                ok.append(int(row["id"]))
            except gallery_shizuku.ShizukuUnavailable:
                deferred.add("android_data_shizuku"); shizuku_available=False
            except Exception as exc:
                errors.append({"id":int(row["id"]),"error":type(exc).__name__})
        return {"ok":True,"indexed_ids":ok,"errors":errors,"hashed_megabytes":round(used/1048576,2),"source_photos_modified":False,
                "deferred_sources":sorted(deferred),"shizuku_available":shizuku_available}
    def exact_groups(args):
        limit=max(1,min(int(args.get("limit",100)),500))
        with db() as c:
            ensure(c);rows=c.execute("""SELECT h.size,h.sha256,COUNT(*) n,GROUP_CONCAT(h.image_id) ids
              FROM gallery_duplicate_hash h JOIN images i ON i.id=h.image_id
              WHERE h.size=i.size AND h.mtime=i.mtime AND h.error IS NULL AND h.sha256 IS NOT NULL
              GROUP BY h.size,h.sha256 HAVING COUNT(*)>1 ORDER BY (COUNT(*)-1)*h.size DESC LIMIT ?""",(limit,)).fetchall()
        groups=[];reclaimable=0
        for r in rows:
            ids=[int(x) for x in r["ids"].split(',')];saved=(len(ids)-1)*int(r["size"]);reclaimable+=saved
            groups.append({"ids":ids,"size":int(r["size"]),"sha256":r["sha256"],"reclaimable_bytes":saved,"confidence":1.0})
        return {"ok":True,"groups":groups,"reclaimable_bytes":reclaimable,"exact":True}
    def near_groups(args):
        threshold=max(0,min(int(args.get("threshold",3)),8));limit=max(1,min(int(args.get("limit",100)),500))
        with db() as c:
            try:rows=c.execute("""SELECT i.id,i.size,v.dhash,v.width,v.height FROM gallery_visual_features v JOIN images i ON i.id=v.image_id
              WHERE v.size=i.size AND v.mtime=i.mtime AND v.error IS NULL AND v.dhash IS NOT NULL""").fetchall()
            except Exception:return {"ok":True,"groups":[],"indexed":0,"threshold":threshold}
        buckets=defaultdict(list);values={}
        for r in rows:
            val=int(r["dhash"],16);values[int(r["id"])]=(val,int(r["size"]),r["width"],r["height"])
            for shift in (0,16,32,48):buckets[(shift,(val>>shift)&0xffff)].append(int(r["id"]))
        parent={i:i for i in values}
        def find(x):
            while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
            return x
        def union(a,b):
            ra,rb=find(a),find(b)
            if ra!=rb:parent[rb]=ra
        seen=set()
        for ids in buckets.values():
            for pos,a in enumerate(ids):
                for b in ids[pos+1:]:
                    pair=(min(a,b),max(a,b))
                    if pair in seen:continue
                    seen.add(pair);va,sa,wa,ha=values[a];vb,sb,wb,hb=values[b]
                    if wa!=wb or ha!=hb:continue
                    if (va^vb).bit_count()<=threshold:union(a,b)
        grouped=defaultdict(list)
        for i in values:grouped[find(i)].append(i)
        groups=[]
        for ids in grouped.values():
            if len(ids)<2:continue
            groups.append({"ids":sorted(ids),"count":len(ids),"threshold":threshold,"confidence":"near_duplicate"})
        groups.sort(key=lambda x:x["count"],reverse=True)
        return {"ok":True,"groups":groups[:limit],"indexed":len(values),"threshold":threshold}
    tools["gallery_duplicate_status"]=(schema(),status)
    tools["gallery_duplicate_index"]=(schema({"limit":{"type":"integer","minimum":1,"maximum":512,"default":64},"max_megabytes":{"type":"integer","minimum":1,"maximum":4096,"default":256}}),index)
    tools["gallery_exact_duplicates"]=(schema({"limit":{"type":"integer","minimum":1,"maximum":500,"default":100}}),exact_groups)
    tools["gallery_near_duplicates"]=(schema({"threshold":{"type":"integer","minimum":0,"maximum":8,"default":3},"limit":{"type":"integer","minimum":1,"maximum":500,"default":100}}),near_groups)
    descriptions.update({"gallery_duplicate_status":"Read persistent exact-duplicate hashing progress.","gallery_duplicate_index":"Incrementally hash same-size candidates for exact duplicate detection.","gallery_exact_duplicates":"List SHA-256 exact duplicate groups and reclaimable bytes.","gallery_near_duplicates":"List visually near-duplicate groups using indexed dHash."})
    readonly.update({"gallery_duplicate_status","gallery_exact_duplicates","gallery_near_duplicates"})
