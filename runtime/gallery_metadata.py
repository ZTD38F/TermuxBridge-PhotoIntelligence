"""Persistent EXIF/technical metadata intelligence.
Author: @dvkrum
Source photos are read-only. Exact GPS coordinates stay local and are omitted unless explicitly requested.
"""
from __future__ import annotations
import datetime,time
from typing import Any
import gallery_shizuku

VERSION="0.5.0"

def _rat(v):
    try:return float(v)
    except Exception:
        try:return float(v.numerator)/float(v.denominator)
        except Exception:return 0.0

def _coord(values,ref):
    try:
        d,m,s=values; value=_rat(d)+_rat(m)/60.0+_rat(s)/3600.0
        return -value if str(ref).upper() in {"S","W"} else value
    except Exception:return None

def _capture_epoch(value):
    if not value:return None
    text=str(value).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S","%Y-%m-%d %H:%M:%S","%Y-%m-%dT%H:%M:%S"):
        try:return datetime.datetime.strptime(text[:19],fmt).timestamp()
        except ValueError:pass
    return None

def install(g:dict[str,Any])->None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"]
    def ensure(c):
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_metadata_index(
          image_id INTEGER PRIMARY KEY,size INTEGER NOT NULL,mtime REAL NOT NULL,processed_at REAL NOT NULL,
          width INTEGER,height INTEGER,taken_text TEXT,taken_epoch REAL,camera_make TEXT,camera_model TEXT,
          exif_orientation INTEGER,gps_lat REAL,gps_lon REAL,gps_alt REAL,error TEXT,
          FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
        c.execute("CREATE INDEX IF NOT EXISTS gallery_metadata_taken_idx ON gallery_metadata_index(taken_epoch)")
        c.execute("CREATE INDEX IF NOT EXISTS gallery_metadata_camera_idx ON gallery_metadata_index(camera_make,camera_model)")
    def decode(row):
        result={"width":None,"height":None,"taken_text":None,"taken_epoch":None,"camera_make":None,"camera_model":None,"exif_orientation":None,"gps_lat":None,"gps_lon":None,"gps_alt":None}
        with g["_materialize_row"](row) as p:
            with g["Image"].open(p) as im:
                result["width"],result["height"]=im.size
                exif=im.getexif()
                for tag in (36867,36868,306):
                    value=exif.get(tag)
                    if value:
                        if isinstance(value,bytes):value=value.decode(errors="replace")
                        result["taken_text"]=str(value)[:64];result["taken_epoch"]=_capture_epoch(result["taken_text"]);break
                for tag,key in ((271,"camera_make"),(272,"camera_model")):
                    value=exif.get(tag)
                    if value:
                        if isinstance(value,bytes):value=value.decode(errors="replace")
                        result[key]=str(value).strip()[:120]
                try:
                    value=exif.get(274)
                    result["exif_orientation"]=int(value) if value is not None else None
                except Exception:pass
                gps={}
                try:
                    gps=exif.get_ifd(34853) if hasattr(exif,"get_ifd") else (exif.get(34853) or {})
                except Exception:gps={}
                if isinstance(gps,dict):
                    lat=_coord(gps.get(2),gps.get(1)) if gps.get(2) else None
                    lon=_coord(gps.get(4),gps.get(3)) if gps.get(4) else None
                    result["gps_lat"],result["gps_lon"]=lat,lon
                    if gps.get(6) is not None:
                        alt=_rat(gps.get(6));result["gps_alt"]=-alt if int(gps.get(5,0) or 0)==1 else alt
        return result
    def status(_):
        with db() as c:
            ensure(c);total=c.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            done=c.execute("""SELECT COUNT(*) FROM gallery_metadata_index m JOIN images i ON i.id=m.image_id
              WHERE m.size=i.size AND m.mtime=i.mtime""").fetchone()[0]
            gps=c.execute("""SELECT COUNT(*) FROM gallery_metadata_index m JOIN images i ON i.id=m.image_id
              WHERE m.size=i.size AND m.mtime=i.mtime AND m.gps_lat IS NOT NULL AND m.gps_lon IS NOT NULL""").fetchone()[0]
            camera=c.execute("""SELECT COUNT(*) FROM gallery_metadata_index m JOIN images i ON i.id=m.image_id
              WHERE m.size=i.size AND m.mtime=i.mtime AND (m.camera_make IS NOT NULL OR m.camera_model IS NOT NULL)""").fetchone()[0]
        return {"ok":True,"total":total,"indexed":done,"remaining":max(0,total-done),"percent":round(done*100/total,2) if total else 100.0,"with_gps":gps,"with_camera":camera,"engine":VERSION}
    def index(args):
        if not g["PIL_AVAILABLE"]:return {"ok":False,"error":"Pillow unavailable"}
        limit=max(1,min(int(args.get("limit",64)),256));ids=args.get("ids");deferred=set();sh_available=True if ids else gallery_shizuku.available_cached(ttl=30.0)
        with db() as c:
            ensure(c)
            if ids:
                rows=[c.execute("SELECT * FROM images WHERE id=?",(int(i),)).fetchone() for i in ids[:64]];rows=[r for r in rows if r]
            else:
                sql="""SELECT i.* FROM images i LEFT JOIN gallery_metadata_index m ON m.image_id=i.id
                       WHERE (m.image_id IS NULL OR m.size<>i.size OR m.mtime<>i.mtime)"""
                params=[]
                if not sh_available:sql+=" AND i.path NOT LIKE 'shizuku://%'";deferred.add("android_data_shizuku")
                sql+=" ORDER BY i.mtime DESC LIMIT ?";params.append(limit);rows=c.execute(sql,params).fetchall()
        ok=[];errors=[]
        for row in rows:
            if "android_data_shizuku" in deferred and gallery_shizuku.is_virtual(str(row["path"])):continue
            try:
                value=decode(row);g["_validate_row"](row)
                with db() as c:
                    ensure(c);c.execute("""INSERT INTO gallery_metadata_index(
                      image_id,size,mtime,processed_at,width,height,taken_text,taken_epoch,camera_make,camera_model,
                      exif_orientation,gps_lat,gps_lon,gps_alt,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
                      ON CONFLICT(image_id) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,processed_at=excluded.processed_at,
                      width=excluded.width,height=excluded.height,taken_text=excluded.taken_text,taken_epoch=excluded.taken_epoch,
                      camera_make=excluded.camera_make,camera_model=excluded.camera_model,exif_orientation=excluded.exif_orientation,
                      gps_lat=excluded.gps_lat,gps_lon=excluded.gps_lon,gps_alt=excluded.gps_alt,error=NULL""",
                      (row["id"],row["size"],row["mtime"],time.time(),value["width"],value["height"],value["taken_text"],value["taken_epoch"],value["camera_make"],value["camera_model"],value["exif_orientation"],value["gps_lat"],value["gps_lon"],value["gps_alt"]))
                ok.append(int(row["id"]))
            except gallery_shizuku.ShizukuUnavailable:
                deferred.add("android_data_shizuku");sh_available=False
            except Exception as exc:
                with db() as c:
                    ensure(c);c.execute("""INSERT INTO gallery_metadata_index(image_id,size,mtime,processed_at,error)
                      VALUES(?,?,?,?,?) ON CONFLICT(image_id) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,
                      processed_at=excluded.processed_at,error=excluded.error""",(row["id"],row["size"],row["mtime"],time.time(),type(exc).__name__))
                errors.append({"id":int(row["id"]),"error":type(exc).__name__})
        return {"ok":True,"indexed_ids":ok,"errors":errors,"deferred_sources":sorted(deferred),"shizuku_available":sh_available,"source_photos_modified":False,"engine":VERSION}
    def _public(row,include_gps=False):
        out={k:row[k] for k in ("image_id","width","height","taken_text","taken_epoch","camera_make","camera_model","exif_orientation","error")}
        out["gps_included"]=bool(include_gps and row["gps_lat"] is not None and row["gps_lon"] is not None)
        if include_gps:
            out.update({"gps_lat":row["gps_lat"],"gps_lon":row["gps_lon"],"gps_alt":row["gps_alt"]})
        return out
    def get(args):
        iid=int(args["id"]);include=bool(args.get("include_gps",False))
        with db() as c:
            ensure(c);row=c.execute("""SELECT m.* FROM gallery_metadata_index m JOIN images i ON i.id=m.image_id
              WHERE m.image_id=? AND m.size=i.size AND m.mtime=i.mtime""",(iid,)).fetchone()
        if row is None:raise ValueError("metadata not indexed for this image")
        return {"ok":True,**_public(row,include)}
    def query(args):
        limit=max(1,min(int(args.get("limit",100)),500));offset=max(0,int(args.get("offset",0)));include=bool(args.get("include_gps",False))
        where=["m.size=i.size","m.mtime=i.mtime"];params=[]
        for key,col in (("date_from","m.taken_epoch"),("min_width","m.width"),("min_height","m.height")):
            if args.get(key) is not None:where.append(f"{col}>=?");params.append(float(args[key]))
        for key,col in (("date_to","m.taken_epoch"),("max_width","m.width"),("max_height","m.height")):
            if args.get(key) is not None:where.append(f"{col}<=?");params.append(float(args[key]))
        for key,col in (("make","m.camera_make"),("model","m.camera_model")):
            if str(args.get(key,"")).strip():where.append(f"lower({col}) LIKE ?");params.append("%"+str(args[key]).strip().lower()+"%")
        if args.get("has_gps") is True:where.append("m.gps_lat IS NOT NULL AND m.gps_lon IS NOT NULL")
        if args.get("has_gps") is False:where.append("(m.gps_lat IS NULL OR m.gps_lon IS NULL)")
        orientation=str(args.get("orientation","")).lower()
        if orientation=="portrait":where.append("m.height>m.width")
        elif orientation=="landscape":where.append("m.width>m.height")
        elif orientation=="square":where.append("m.width=m.height")
        sql=" AND ".join(where)
        with db() as c:
            ensure(c);total=c.execute(f"SELECT COUNT(*) FROM images i JOIN gallery_metadata_index m ON m.image_id=i.id WHERE {sql}",params).fetchone()[0]
            rows=c.execute(f"""SELECT i.*,m.width mw,m.height mh,m.taken_text,m.taken_epoch,m.camera_make,m.camera_model,m.exif_orientation,m.gps_lat,m.gps_lon,m.gps_alt,m.error
              FROM images i JOIN gallery_metadata_index m ON m.image_id=i.id WHERE {sql}
              ORDER BY COALESCE(m.taken_epoch,i.mtime) DESC LIMIT ? OFFSET ?""",[*params,limit,offset]).fetchall()
        items=[]
        for r in rows:
            item=g["_item"](r);item.update({"width":r["mw"],"height":r["mh"],"taken_text":r["taken_text"],"taken_epoch":r["taken_epoch"],"camera_make":r["camera_make"],"camera_model":r["camera_model"],"exif_orientation":r["exif_orientation"],"gps_included":bool(include and r["gps_lat"] is not None)})
            if include:item.update({"gps_lat":r["gps_lat"],"gps_lon":r["gps_lon"],"gps_alt":r["gps_alt"]})
            items.append(item)
        return {"ok":True,"total":total,"count":len(items),"offset":offset,"next_offset":offset+len(items) if offset+len(items)<total else None,"items":items}
    tools["gallery_metadata_index"]=(schema({"ids":{"type":"array","items":{"type":"integer","minimum":1},"maxItems":64},"limit":{"type":"integer","minimum":1,"maximum":256,"default":64}}),index)
    tools["gallery_metadata_status"]=(schema(),status)
    tools["gallery_metadata_get"]=(schema({"id":{"type":"integer","minimum":1},"include_gps":{"type":"boolean","default":False}},["id"]),get)
    tools["gallery_metadata_query"]=(schema({"date_from":{"type":"number"},"date_to":{"type":"number"},"make":{"type":"string"},"model":{"type":"string"},"has_gps":{"type":"boolean"},"orientation":{"type":"string","enum":["portrait","landscape","square"]},"min_width":{"type":"integer","minimum":1},"max_width":{"type":"integer","minimum":1},"min_height":{"type":"integer","minimum":1},"max_height":{"type":"integer","minimum":1},"include_gps":{"type":"boolean","default":False},"limit":{"type":"integer","minimum":1,"maximum":500,"default":100},"offset":{"type":"integer","minimum":0,"default":0}}),query)
    descriptions.update({"gallery_metadata_index":"Incrementally index local EXIF/camera/dimensions metadata. Source photos stay read-only.","gallery_metadata_status":"Read persistent technical metadata coverage.","gallery_metadata_get":"Read indexed metadata for one photo; exact GPS is omitted unless include_gps=true.","gallery_metadata_query":"Query indexed photos by capture date, camera, dimensions, orientation, or GPS presence."})
    readonly.update({"gallery_metadata_status","gallery_metadata_get","gallery_metadata_query"})
