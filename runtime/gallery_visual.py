"""Local visual feature index for Photo Intelligence.
Author: @dvkrum
"""
from __future__ import annotations
import math, statistics, time
from typing import Any
import gallery_shizuku
from gallery_decode import open_image
VERSION = "0.1.2"

def install(g: dict[str, Any]) -> None:
    schema=g["_schema"]; tools=g["TOOLS"]; descriptions=g["DESCRIPTIONS"]; readonly=g["READ_ONLY"]; db=g["_db"]
    def ensure(c):
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_visual_features(
          image_id INTEGER PRIMARY KEY,size INTEGER NOT NULL,mtime REAL NOT NULL,processed_at REAL NOT NULL,
          brightness REAL,contrast REAL,sharpness REAL,entropy REAL,dominant_color TEXT,screenshot_score REAL,
          width INTEGER,height INTEGER,dhash TEXT,error TEXT,FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
        cols={r[1] for r in c.execute("PRAGMA table_info(gallery_visual_features)")}
        if "dhash" not in cols: c.execute("ALTER TABLE gallery_visual_features ADD COLUMN dhash TEXT")
    def color_name(rgb):
        r,gc,b=rgb; mx=max(rgb); mn=min(rgb)
        if mx<55:return "black"
        if mn>205:return "white"
        if mx-mn<28:return "gray"
        if r>gc*1.25 and r>b*1.25:return "red"
        if gc>r*1.18 and gc>b*1.12:return "green"
        if b>r*1.18 and b>gc*1.12:return "blue"
        if r>150 and gc>105 and b<105:return "orange"
        if r>140 and b>120 and gc<120:return "purple"
        if r>150 and gc>145 and b<120:return "yellow"
        if r>150 and b>145 and gc>110:return "pink"
        return "mixed"
    def metrics(row):
        if not g["PIL_AVAILABLE"]: raise RuntimeError("Pillow unavailable")
        with g["_materialize_row"](row) as p:
            with open_image(g["Image"], p) as src:
                img=g["ImageOps"].exif_transpose(src).convert("RGB"); width,height=img.size
                thumb=img.copy(); thumb.thumbnail((256,256),g["Image"].Resampling.BILINEAR)
                gray=thumb.convert("L"); pixels=list(gray.get_flattened_data()) if hasattr(gray,"get_flattened_data") else list(gray.getdata())
                if not pixels: raise ValueError("empty image")
                brightness=sum(pixels)/len(pixels); contrast=statistics.pstdev(pixels) if len(pixels)>1 else 0.0
                hist=gray.histogram(); n=sum(hist); entropy=0.0
                for count in hist:
                    if count:
                        p0=count/n; entropy-=p0*math.log2(p0)
                w,h=gray.size; diffs=[]
                for y in range(h):
                    base=y*w
                    for x in range(1,w): diffs.append(abs(pixels[base+x]-pixels[base+x-1]))
                sharpness=sum(diffs)/len(diffs) if diffs else 0.0
                rgb=thumb.resize((1,1),g["Image"].Resampling.BOX).getpixel((0,0))
                dh=gray.resize((9,8),g["Image"].Resampling.LANCZOS); dp=list(dh.get_flattened_data()) if hasattr(dh,"get_flattened_data") else list(dh.getdata()); bits=0
                for yy in range(8):
                    for xx in range(8): bits=(bits<<1)|int(dp[yy*9+xx]>dp[yy*9+xx+1])
                path=str(row["path"]).lower(); score=0.8 if "screenshot" in path else 0.0
                ratio=max(width,height)/max(1,min(width,height))
                if 1.65<=ratio<=2.35: score+=0.15
                return {"brightness":round(brightness,2),"contrast":round(contrast,2),"sharpness":round(sharpness,2),
                        "entropy":round(entropy,3),"dominant_color":color_name(rgb),"screenshot_score":min(1.0,round(score,2)),
                        "width":width,"height":height,"dhash":f"{bits:016x}"}
    def visual_index(args):
        limit=max(1,min(int(args.get("limit",64)),256)); ids=args.get("ids"); deferred=set()
        shizuku_available=True if ids else gallery_shizuku.available_cached(ttl=30.0)
        with db() as c:
            ensure(c)
            if ids:
                rows=[c.execute("SELECT * FROM images WHERE id=?",(int(i),)).fetchone() for i in ids[:64]]; rows=[r for r in rows if r]
            else:
                sql="""SELECT i.* FROM images i LEFT JOIN gallery_visual_features v ON v.image_id=i.id
                  WHERE (v.image_id IS NULL OR v.size<>i.size OR v.mtime<>i.mtime)"""
                params=[]
                if not shizuku_available:
                    sql += " AND i.path NOT LIKE 'shizuku://%'"; deferred.add("android_data_shizuku")
                sql += " ORDER BY i.mtime DESC LIMIT ?"; params.append(limit)
                rows=c.execute(sql,params).fetchall()
        ok=[]; errors=[]
        for row in rows:
            if "android_data_shizuku" in deferred and gallery_shizuku.is_virtual(str(row["path"])): continue
            try:
                v=metrics(row); g["_validate_row"](row)
                with db() as c:
                    ensure(c)
                    c.execute("""INSERT INTO gallery_visual_features(image_id,size,mtime,processed_at,brightness,contrast,sharpness,entropy,dominant_color,screenshot_score,width,height,dhash,error)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(image_id) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,processed_at=excluded.processed_at,
                      brightness=excluded.brightness,contrast=excluded.contrast,sharpness=excluded.sharpness,entropy=excluded.entropy,dominant_color=excluded.dominant_color,
                      screenshot_score=excluded.screenshot_score,width=excluded.width,height=excluded.height,dhash=excluded.dhash,error=NULL""",
                      (row["id"],row["size"],row["mtime"],time.time(),v["brightness"],v["contrast"],v["sharpness"],v["entropy"],v["dominant_color"],v["screenshot_score"],v["width"],v["height"],v["dhash"]))
                ok.append(int(row["id"]))
            except gallery_shizuku.ShizukuUnavailable:
                deferred.add("android_data_shizuku"); shizuku_available=False
            except g["UnidentifiedImageError"]:
                # A missing Pillow decoder is not a reason to retry the same file
                # on every short scheduler cycle. Never touch the source file.
                try:
                    g["_validate_row"](row)
                    with db() as c:
                        ensure(c)
                        c.execute("""INSERT INTO gallery_visual_features(
                            image_id,size,mtime,processed_at,error)
                            VALUES(?,?,?,?,'UnidentifiedImageError')
                            ON CONFLICT(image_id) DO UPDATE SET
                            size=excluded.size,mtime=excluded.mtime,
                            processed_at=excluded.processed_at,
                            brightness=NULL,contrast=NULL,sharpness=NULL,entropy=NULL,
                            dominant_color=NULL,screenshot_score=NULL,
                            width=NULL,height=NULL,dhash=NULL,error=excluded.error""",
                            (row["id"],row["size"],row["mtime"],time.time()))
                except Exception as exc:
                    errors.append({"id":int(row["id"]),"error":type(exc).__name__})
                else:
                    errors.append({"id":int(row["id"]),"error":"UnidentifiedImageError"})
            except Exception as exc:
                errors.append({"id":int(row["id"]),"error":type(exc).__name__})
        return {"ok":True,"indexed_ids":ok,"errors":errors,"engine":VERSION,"source_photos_modified":False,
                "deferred_sources":sorted(deferred),"shizuku_available":shizuku_available}
    def visual_status(_):
        with db() as c:
            ensure(c); total=c.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            sh_total=c.execute("SELECT COUNT(*) FROM images WHERE path LIKE 'shizuku://%'").fetchone()[0]
            done=c.execute("SELECT COUNT(*) FROM gallery_visual_features v JOIN images i ON i.id=v.image_id WHERE v.size=i.size AND v.mtime=i.mtime AND v.error IS NULL").fetchone()[0]
            failed=c.execute("SELECT COUNT(*) FROM gallery_visual_features v JOIN images i ON i.id=v.image_id WHERE v.size=i.size AND v.mtime=i.mtime AND v.error IS NOT NULL").fetchone()[0]
            sh_done=c.execute("SELECT COUNT(*) FROM gallery_visual_features v JOIN images i ON i.id=v.image_id WHERE v.size=i.size AND v.mtime=i.mtime AND v.error IS NULL AND i.path LIKE 'shizuku://%'").fetchone()[0]
            sh_failed=c.execute("SELECT COUNT(*) FROM gallery_visual_features v JOIN images i ON i.id=v.image_id WHERE v.size=i.size AND v.mtime=i.mtime AND v.error IS NOT NULL AND i.path LIKE 'shizuku://%'").fetchone()[0]
        local_total=total-sh_total; local_done=done-sh_done; local_failed=failed-sh_failed
        return {"ok":True,"total":total,"indexed":done,"failed":failed,"remaining":max(0,total-done-failed),"percent":round(done*100/total,2) if total else 100.0,
                "local":{"total":local_total,"indexed":local_done,"failed":local_failed,"remaining":max(0,local_total-local_done-local_failed)},
                "android_data":{"total":sh_total,"indexed":sh_done,"failed":sh_failed,"remaining":max(0,sh_total-sh_done-sh_failed),"available":gallery_shizuku.available_cached(ttl=30.0)},
                "engine":VERSION,"semantic_backend":"not_configured"}
    def visual_search(args):
        color=str(args.get("color","")).strip().lower(); screenshot=args.get("screenshot"); limit=max(1,min(int(args.get("limit",100)),500))
        where=["v.error IS NULL","v.size=i.size","v.mtime=i.mtime"]; params=[]
        if color: where.append("v.dominant_color=?"); params.append(color)
        if screenshot is True: where.append("v.screenshot_score>=0.5")
        if screenshot is False: where.append("v.screenshot_score<0.5")
        params.append(limit)
        with db() as c:
            ensure(c); rows=c.execute(f"SELECT i.*,v.brightness,v.contrast,v.sharpness,v.entropy,v.dominant_color,v.screenshot_score FROM images i JOIN gallery_visual_features v ON v.image_id=i.id WHERE {' AND '.join(where)} ORDER BY i.mtime DESC LIMIT ?",params).fetchall()
        return {"ok":True,"count":len(rows),"items":[{**g["_item"](r),"brightness":r["brightness"],"contrast":r["contrast"],"sharpness":r["sharpness"],"entropy":r["entropy"],"dominant_color":r["dominant_color"],"screenshot_score":r["screenshot_score"]} for r in rows]}
    tools["gallery_visual_index"]=(schema({"ids":{"type":"array","items":{"type":"integer","minimum":1},"maxItems":64},"limit":{"type":"integer","minimum":1,"maximum":256,"default":64}}),visual_index)
    tools["gallery_visual_status"]=(schema(),visual_status)
    tools["gallery_visual_search"]=(schema({"color":{"type":"string"},"screenshot":{"type":"boolean"},"limit":{"type":"integer","minimum":1,"maximum":500,"default":100}}),visual_search)
    descriptions.update({"gallery_visual_index":"Build local visual and quality features without cloud calls.","gallery_visual_status":"Read visual feature coverage by source.","gallery_visual_search":"Search indexed visual features such as dominant color and screenshot likelihood."})
    readonly.update({"gallery_visual_status","gallery_visual_search"})
