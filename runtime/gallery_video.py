"""Bounded local video intelligence using ffprobe/ffmpeg keyframes.
Author: @dvkrum
Source videos are read-only.
"""
from __future__ import annotations
import base64,json,os,shutil,subprocess,time
from array import array
from contextlib import contextmanager
from pathlib import Path
from typing import Any
import gallery_shizuku
from gallery_semantic import MODEL_ID,DIM,available as semantic_available,path_embedding,text_embedding

VERSION="0.7.0"
VIDEO_EXTS={".mp4",".m4v",".mov",".mkv",".webm",".avi",".3gp",".3g2",".mts",".m2ts"}
MIME={".mp4":"video/mp4",".m4v":"video/x-m4v",".mov":"video/quicktime",".mkv":"video/x-matroska",".webm":"video/webm",".avi":"video/x-msvideo",".3gp":"video/3gpp",".3g2":"video/3gpp2",".mts":"video/mp2t",".m2ts":"video/mp2t"}

def _safe_name(value):return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(value))[:120]

def install(g:dict[str,Any])->None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"];root=g["SHARED_ROOT"].resolve();state=g["STATE_DIR"];cache=(state/"video-keyframes").resolve()
    def ensure(c):
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_videos(
          id INTEGER PRIMARY KEY,path TEXT NOT NULL UNIQUE,name TEXT NOT NULL,album TEXT NOT NULL,size INTEGER NOT NULL,
          mtime REAL NOT NULL,seen INTEGER NOT NULL,mime TEXT,duration REAL,width INTEGER,height INTEGER,codec TEXT,
          processed_at REAL,error TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS gallery_videos_mtime_idx ON gallery_videos(mtime DESC)")
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_video_frames(
          video_id INTEGER NOT NULL,frame_no INTEGER NOT NULL,timestamp REAL NOT NULL,source_size INTEGER NOT NULL,
          source_mtime REAL NOT NULL,jpeg_path TEXT NOT NULL,embedding BLOB,ocr_text TEXT,processed_at REAL NOT NULL,error TEXT,
          PRIMARY KEY(video_id,frame_no),FOREIGN KEY(video_id) REFERENCES gallery_videos(id) ON DELETE CASCADE)""")
        c.execute("CREATE INDEX IF NOT EXISTS gallery_video_frames_source_idx ON gallery_video_frames(video_id,source_size,source_mtime)")
    def _item(r):
        return {"id":r["id"],"name":r["name"],"album":r["album"],"path":r["path"],"size":r["size"],"mtime":r["mtime"],"mime":r["mime"],"duration":r["duration"],"width":r["width"],"height":r["height"],"codec":r["codec"],"error":r["error"]}
    def _validate(row):
        value=str(row["path"])
        if gallery_shizuku.is_virtual(value):
            gallery_shizuku.validate(value,int(row["size"]),float(row["mtime"]));return
        p=root/value
        if p.is_symlink():raise ValueError("symbolic links are not permitted")
        real=p.resolve()
        if not real.is_file() or not real.is_relative_to(root):raise ValueError("video source unavailable; rescan")
        st=real.stat()
        if st.st_size!=int(row["size"]) or abs(st.st_mtime-float(row["mtime"]))>0.00001:raise ValueError("video source changed; rescan")
    @contextmanager
    def materialize(row):
        value=str(row["path"])
        if gallery_shizuku.is_virtual(value):
            with gallery_shizuku.materialize(value,int(row["size"]),float(row["mtime"]),state) as p:yield p
        else:
            _validate(row);yield (root/value).resolve()
    def scan(_):
        started=time.time();stamp=time.time_ns();candidates=[];errors=[];remote_deferred=False
        if not root.is_dir():return {"ok":False,"error":"shared storage unavailable; video index preserved"}
        for parent,dirs,files in os.walk(root,followlinks=False):
            pp=Path(parent);kept=[]
            for d in dirs:
                child=pp/d
                if child.is_symlink():continue
                rel=str(child.relative_to(root)).replace("\\","/").lower()
                if rel=="android/data" or rel.startswith("android/data/") or rel==".photo-intelligence-quarantine" or rel.startswith(".photo-intelligence-quarantine/"):continue
                kept.append(d)
            dirs[:]=kept
            for name in files:
                p=pp/name
                if p.suffix.lower() not in VIDEO_EXTS or p.is_symlink():continue
                try:
                    st=p.stat()
                    if st.st_size<=0:continue
                    rel=str(p.relative_to(root));album=str(p.parent.relative_to(root));candidates.append((rel,name,album if album!="." else "Shared storage",int(st.st_size),float(st.st_mtime),stamp,MIME.get(p.suffix.lower())))
                except (OSError,ValueError) as exc:
                    if len(errors)<12:errors.append(type(exc).__name__)
        remote_count=0
        if root==Path("/storage/emulated/0").resolve():
            if gallery_shizuku.available_cached(ttl=30.0):
                try:
                    for absolute,size,mtime in gallery_shizuku.enumerate_files(VIDEO_EXTS):
                        rel=absolute[len("/storage/emulated/0/"):];p=Path(absolute);candidates.append((gallery_shizuku.to_virtual(absolute),p.name,str(Path(rel).parent),int(size),float(mtime),stamp,MIME.get(p.suffix.lower())));remote_count+=1
                except gallery_shizuku.ShizukuUnavailable:remote_deferred=True
            else:remote_deferred=True
        with db() as c:
            ensure(c);old=c.execute("SELECT COUNT(*) FROM gallery_videos").fetchone()[0];expected=c.execute("SELECT COUNT(*) FROM gallery_videos WHERE path NOT LIKE 'shizuku://%'").fetchone()[0] if remote_deferred else old
        local_found=sum(1 for x in candidates if not str(x[0]).startswith("shizuku://")) if remote_deferred else len(candidates)
        if expected>=50 and local_found<expected*.5:return {"ok":False,"error":"video scan unexpectedly incomplete; index preserved","found":len(candidates),"previous":old}
        with db() as c:
            ensure(c);c.executemany("""INSERT INTO gallery_videos(path,name,album,size,mtime,seen,mime) VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(path) DO UPDATE SET name=excluded.name,album=excluded.album,size=excluded.size,mtime=excluded.mtime,
              seen=excluded.seen,mime=excluded.mime,
              duration=CASE WHEN gallery_videos.size<>excluded.size OR gallery_videos.mtime<>excluded.mtime THEN NULL ELSE gallery_videos.duration END,
              width=CASE WHEN gallery_videos.size<>excluded.size OR gallery_videos.mtime<>excluded.mtime THEN NULL ELSE gallery_videos.width END,
              height=CASE WHEN gallery_videos.size<>excluded.size OR gallery_videos.mtime<>excluded.mtime THEN NULL ELSE gallery_videos.height END,
              codec=CASE WHEN gallery_videos.size<>excluded.size OR gallery_videos.mtime<>excluded.mtime THEN NULL ELSE gallery_videos.codec END,
              processed_at=CASE WHEN gallery_videos.size<>excluded.size OR gallery_videos.mtime<>excluded.mtime THEN NULL ELSE gallery_videos.processed_at END,
              error=CASE WHEN gallery_videos.size<>excluded.size OR gallery_videos.mtime<>excluded.mtime THEN NULL ELSE gallery_videos.error END""",candidates)
            stale=[r[0] for r in c.execute("""SELECT DISTINCT f.video_id FROM gallery_video_frames f JOIN gallery_videos v ON v.id=f.video_id
              WHERE f.source_size<>v.size OR f.source_mtime<>v.mtime""").fetchall()]
            if stale:
                q="DELETE FROM gallery_video_frames WHERE video_id IN (%s)"%",".join("?"*len(stale));c.execute(q,stale)
            removed=c.execute("DELETE FROM gallery_videos WHERE seen<>? AND path NOT LIKE 'shizuku://%'",(stamp,)).rowcount if remote_deferred else c.execute("DELETE FROM gallery_videos WHERE seen<>?",(stamp,)).rowcount
            total=c.execute("SELECT COUNT(*) FROM gallery_videos").fetchone()[0]
        for vid in stale:
            shutil.rmtree(cache/str(vid),ignore_errors=True)
        return {"ok":True,"indexed":total,"scanned":len(candidates),"removed_missing":removed,"remote_android_data":remote_count,"deferred_sources":["android_data_shizuku"] if remote_deferred else [],"seconds":round(time.time()-started,2),"source_videos_modified":False}
    def probe(path):
        p=subprocess.run(["ffprobe","-v","error","-print_format","json","-show_streams","-show_format",str(path)],capture_output=True,text=True,timeout=30,check=False)
        if p.returncode!=0:raise RuntimeError("ffprobe failed")
        data=json.loads(p.stdout);stream=next((x for x in data.get("streams",[]) if x.get("codec_type")=="video"),{})
        duration=float(data.get("format",{}).get("duration") or stream.get("duration") or 0.0)
        return {"duration":max(0.0,duration),"width":int(stream.get("width") or 0) or None,"height":int(stream.get("height") or 0) or None,"codec":str(stream.get("codec_name") or "")[:60] or None}
    def timestamps(duration,n):
        if duration<=0:return [0.0]
        if duration<2:return [min(.1,duration/2)]
        n=max(1,min(n,8));return [round(min(duration-.05,duration*(i+.5)/n),3) for i in range(n)]
    def frame(path,out,ts):
        out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        tmp=out.with_name("."+out.name+".tmp.jpg")
        p=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-ss",str(ts),"-i",str(path),"-frames:v","1","-vf","scale=640:-2:force_original_aspect_ratio=decrease","-q:v","4","-y",str(tmp)],capture_output=True,timeout=45,check=False)
        if p.returncode!=0 or not tmp.is_file() or tmp.stat().st_size<=0:
            try:tmp.unlink()
            except OSError:pass
            raise RuntimeError("ffmpeg keyframe failed")
        os.replace(tmp,out);out.chmod(0o600)
    def ocr(path,languages):
        if not shutil.which("tesseract"):return ""
        p=subprocess.run(["tesseract",str(path),"stdout","-l",languages,"--psm","6"],capture_output=True,text=True,timeout=30,check=False)
        return " ".join(p.stdout.split())[:12000] if p.returncode==0 else ""
    def status(_):
        with db() as c:
            ensure(c);total=c.execute("SELECT COUNT(*) FROM gallery_videos").fetchone()[0]
            analyzed=c.execute("SELECT COUNT(*) FROM gallery_videos WHERE processed_at IS NOT NULL AND error IS NULL").fetchone()[0]
            frames=c.execute("""SELECT COUNT(*) FROM gallery_video_frames f JOIN gallery_videos v ON v.id=f.video_id
              WHERE f.source_size=v.size AND f.source_mtime=v.mtime AND f.error IS NULL""").fetchone()[0]
            semantic=c.execute("""SELECT COUNT(*) FROM gallery_video_frames f JOIN gallery_videos v ON v.id=f.video_id
              WHERE f.source_size=v.size AND f.source_mtime=v.mtime AND f.embedding IS NOT NULL""").fetchone()[0]
            remote=c.execute("SELECT COUNT(*) FROM gallery_videos WHERE path LIKE 'shizuku://%'").fetchone()[0]
        return {"ok":True,"total":total,"analyzed":analyzed,"remaining":max(0,total-analyzed),"percent":round(analyzed*100/total,2) if total else 100.0,"keyframes":frames,"semantic_keyframes":semantic,"android_data":remote,"android_data_available":gallery_shizuku.available_cached(ttl=30.0),"engine":VERSION}
    def index(args):
        limit=max(1,min(int(args.get("limit",1)),16));nframes=max(1,min(int(args.get("frames",4)),8));max_bytes=max(16,min(int(args.get("max_megabytes",512)),4096))*1048576;languages=str(args.get("languages","eng"))
        semantic_on=semantic_available();sh_available=gallery_shizuku.available_cached(ttl=30.0)
        with db() as c:
            ensure(c)
            sql="""SELECT v.* FROM gallery_videos v WHERE
              v.processed_at IS NULL OR v.error IS NOT NULL OR
              NOT EXISTS(SELECT 1 FROM gallery_video_frames f WHERE f.video_id=v.id AND f.source_size=v.size AND f.source_mtime=v.mtime)"""
            if semantic_on:sql+=" OR EXISTS(SELECT 1 FROM gallery_video_frames f WHERE f.video_id=v.id AND f.source_size=v.size AND f.source_mtime=v.mtime AND f.embedding IS NULL)"
            if not sh_available:sql="SELECT * FROM ("+sql+") q WHERE q.path NOT LIKE 'shizuku://%'"
            sql+=" ORDER BY mtime DESC LIMIT ?";rows=c.execute(sql,(limit,)).fetchall()
        done=[];errors=[];deferred=[] if sh_available else ["android_data_shizuku"]
        for row in rows:
            if int(row["size"])>max_bytes:
                with db() as c:ensure(c);c.execute("UPDATE gallery_videos SET processed_at=?,error=? WHERE id=?",(time.time(),"VIDEO_TOO_LARGE_FOR_ANALYSIS",row["id"]))
                errors.append({"id":row["id"],"error":"VIDEO_TOO_LARGE_FOR_ANALYSIS"});continue
            try:
                _validate(row)
                with materialize(row) as local:
                    meta=probe(local);times=timestamps(meta["duration"],nframes);folder=(cache/str(row["id"])).resolve()
                    if not folder.is_relative_to(cache):raise ValueError("cache escape")
                    for idx,ts in enumerate(times):
                        out=folder/f"{idx:02d}.jpg";frame(local,out,ts);text=ocr(out,languages);emb=path_embedding(g,out).astype("float32").tobytes() if semantic_on else None
                        with db() as c:
                            ensure(c);c.execute("""INSERT INTO gallery_video_frames(video_id,frame_no,timestamp,source_size,source_mtime,jpeg_path,embedding,ocr_text,processed_at,error)
                              VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(video_id,frame_no) DO UPDATE SET timestamp=excluded.timestamp,
                              source_size=excluded.source_size,source_mtime=excluded.source_mtime,jpeg_path=excluded.jpeg_path,
                              embedding=excluded.embedding,ocr_text=excluded.ocr_text,processed_at=excluded.processed_at,error=NULL""",
                              (row["id"],idx,ts,row["size"],row["mtime"],str(out.relative_to(state)),emb,text,time.time()))
                    with db() as c:
                        ensure(c);c.execute("UPDATE gallery_videos SET duration=?,width=?,height=?,codec=?,processed_at=?,error=NULL WHERE id=?",(meta["duration"],meta["width"],meta["height"],meta["codec"],time.time(),row["id"]))
                done.append(int(row["id"]))
            except gallery_shizuku.ShizukuUnavailable:
                if "android_data_shizuku" not in deferred:deferred.append("android_data_shizuku")
            except Exception as exc:
                with db() as c:ensure(c);c.execute("UPDATE gallery_videos SET processed_at=?,error=? WHERE id=?",(time.time(),type(exc).__name__,row["id"]))
                errors.append({"id":int(row["id"]),"error":type(exc).__name__})
        return {"ok":True,"indexed_ids":done,"errors":errors,"deferred_sources":deferred,"semantic":semantic_on,"source_videos_modified":False}
    def frames(args):
        vid=int(args["id"])
        with db() as c:
            ensure(c);v=c.execute("SELECT * FROM gallery_videos WHERE id=?",(vid,)).fetchone()
            if not v:raise ValueError("unknown video id")
            rows=c.execute("""SELECT frame_no,timestamp,jpeg_path,ocr_text,embedding IS NOT NULL semantic,error FROM gallery_video_frames
              WHERE video_id=? AND source_size=? AND source_mtime=? ORDER BY frame_no""",(vid,v["size"],v["mtime"])).fetchall()
        return {"ok":True,"video":_item(v),"frames":[{"frame_no":r["frame_no"],"timestamp":r["timestamp"],"ocr_text":r["ocr_text"],"semantic":bool(r["semantic"]),"error":r["error"]} for r in rows]}
    def keyframe(args):
        vid=int(args["id"]);frame_no=int(args.get("frame_no",0))
        with db() as c:
            ensure(c);r=c.execute("""SELECT f.*,v.name FROM gallery_video_frames f JOIN gallery_videos v ON v.id=f.video_id
              WHERE f.video_id=? AND f.frame_no=? AND f.source_size=v.size AND f.source_mtime=v.mtime""",(vid,frame_no)).fetchone()
        if not r:raise ValueError("keyframe unavailable")
        p=(state/r["jpeg_path"]).resolve()
        if not p.is_file() or not p.is_relative_to(state):raise ValueError("keyframe cache unavailable")
        data=p.read_bytes()
        return {"__mcp_content__":[{"type":"image","data":base64.b64encode(data).decode("ascii"),"mimeType":"image/jpeg"},{"type":"text","text":json.dumps({"video_id":vid,"frame_no":frame_no,"timestamp":r["timestamp"],"name":r["name"]},ensure_ascii=False)}]}
    def search(args):
        query=str(args["query"]).strip()
        if not query:raise ValueError("query required")
        limit=max(1,min(int(args.get("limit",50)),200));qtext=query.lower();sem_q=text_embedding(query) if semantic_available() else None
        with db() as c:
            ensure(c);videos=c.execute("SELECT * FROM gallery_videos").fetchall();frs=c.execute("""SELECT f.* FROM gallery_video_frames f JOIN gallery_videos v ON v.id=f.video_id
              WHERE f.source_size=v.size AND f.source_mtime=v.mtime""").fetchall()
        by={}
        for v in videos:
            score=0.0;reasons=[]
            hay=(str(v["name"])+" "+str(v["album"])+" "+str(v["path"])).lower()
            if qtext in hay:score+=3.0;reasons.append("metadata")
            by[int(v["id"])]={"video":v,"score":score,"reasons":reasons,"best_timestamp":None,"semantic_score":None}
        for f in frs:
            slot=by.get(int(f["video_id"]))
            if not slot:continue
            text=(f["ocr_text"] or "").lower()
            if qtext in text:
                slot["score"]+=2.5;slot["reasons"].append("ocr");slot["best_timestamp"]=f["timestamp"]
            if sem_q is not None and f["embedding"]:
                vec=array("f");vec.frombytes(f["embedding"])
                if len(vec)==DIM:
                    sc=sum(float(a)*float(b) for a,b in zip(sem_q,vec))
                    if slot["semantic_score"] is None or sc>slot["semantic_score"]:
                        slot["semantic_score"]=sc;slot["best_timestamp"]=f["timestamp"]
        out=[]
        for slot in by.values():
            if slot["semantic_score"] is not None:
                slot["score"]+=max(0.0,float(slot["semantic_score"]))*2.0;slot["reasons"].append("semantic")
            if slot["score"]>0:
                item=_item(slot["video"]);item.update({"score":round(slot["score"],5),"match":sorted(set(slot["reasons"])),"best_timestamp":slot["best_timestamp"],"semantic_score":round(slot["semantic_score"],5) if slot["semantic_score"] is not None else None});out.append(item)
        out.sort(key=lambda x:(x["score"],x["mtime"]),reverse=True)
        return {"ok":True,"query":query,"count":min(len(out),limit),"items":out[:limit],"semantic_available":sem_q is not None}
    tools["gallery_video_scan"]=(schema(),scan)
    tools["gallery_video_status"]=(schema(),status)
    tools["gallery_video_index"]=(schema({"limit":{"type":"integer","minimum":1,"maximum":16,"default":1},"frames":{"type":"integer","minimum":1,"maximum":8,"default":4},"max_megabytes":{"type":"integer","minimum":16,"maximum":4096,"default":512},"languages":{"type":"string","default":"eng"}}),index)
    tools["gallery_video_search"]=(schema({"query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":200,"default":50}},["query"]),search)
    tools["gallery_video_frames"]=(schema({"id":{"type":"integer","minimum":1}},["id"]),frames)
    tools["gallery_video_keyframe"]=(schema({"id":{"type":"integer","minimum":1},"frame_no":{"type":"integer","minimum":0,"maximum":7,"default":0}},["id"]),keyframe)
    descriptions.update({"gallery_video_scan":"Refresh the read-only local video catalog, including Android/data when Shizuku is available.","gallery_video_status":"Read bounded video/keyframe intelligence progress.","gallery_video_index":"Extract bounded representative keyframes, OCR and local CLIP embeddings without modifying source videos.","gallery_video_search":"Hybrid search over video filename/path, keyframe OCR and local CLIP visual meaning.","gallery_video_frames":"List analyzed keyframes and timestamps for one video.","gallery_video_keyframe":"Return one cached representative keyframe for visual inspection."})
    readonly.update({"gallery_video_status","gallery_video_search","gallery_video_frames","gallery_video_keyframe"})
