#!/usr/bin/env python3
"""Private, fully local face detection & conservative person grouping.

YuNet / SFace model weights are user-local only, with verified upstream SHA256.
No original photo modification, cloud APIs, hidden Android/data, or naming real
people automatically. Stores sensitive facial vectors in Termux private 0600 DB.
Author: @dvkrum.
"""
from __future__ import annotations

import argparse
import hashlib
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from typing import Any

# Lazily import ONNX + NumPy: indexing/meta and tests do not need model RAM.
SHARED_ROOT = Path("/storage/emulated/0")
STATE = Path.home()/".termux-mcp-bridge/addons/photo-intelligence/state"
MODEL_ROOT = Path.home()/".local/share/photo-intelligence-tools/face-models"
MODELS = {
    "face_detection_yunet_2023mar.onnx":
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "face_recognition_sface_2021dec.onnx":
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}
VERSION = "yunet-2023mar-sface-2021dec-v1"
# Conservative similarity: favors splitting one person over merging different people.
STRICT_CLUSTER_COSINE = 0.57
MIN_FACE_SCORE = 0.72
MIN_FACE_EDGE = 30
MAX_FILE_BYTES = 160*1024*1024
MAX_DECODE_PIXELS = 150_000_000
MODEL_WIDTH = 640

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sqlite_ro(path: Path) -> sqlite3.Connection:
    path=path.resolve(strict=True)
    con=sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=12)
    con.execute("PRAGMA query_only=ON")
    return con


def setup_private_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():
        raise ValueError("face index cannot be symlink")
    if path.exists():
        path.chmod(0o600)
    con=sqlite3.connect(path,timeout=20)
    path.chmod(0o600)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS face_scans(
      image_id INTEGER PRIMARY KEY,
      path TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
      algorithm TEXT NOT NULL, status TEXT NOT NULL,
      face_count INTEGER NOT NULL DEFAULT 0, checked_at TEXT NOT NULL,
      error_code TEXT
    );
    CREATE TABLE IF NOT EXISTS face_embeddings(
      face_id INTEGER PRIMARY KEY AUTOINCREMENT,
      image_id INTEGER NOT NULL, face_ordinal INTEGER NOT NULL,
      x REAL NOT NULL, y REAL NOT NULL, width REAL NOT NULL, height REAL NOT NULL,
      score REAL NOT NULL, vector BLOB NOT NULL, algorithm TEXT NOT NULL,
      UNIQUE(image_id,face_ordinal)
    );
    CREATE INDEX IF NOT EXISTS face_embeddings_image_idx
      ON face_embeddings(image_id);
    CREATE TABLE IF NOT EXISTS face_clusters(
      face_id INTEGER PRIMARY KEY,
      cluster_id INTEGER NOT NULL,
      assigned_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS face_clusters_group_idx ON face_clusters(cluster_id);
    CREATE TABLE IF NOT EXISTS face_manual_names(
      pinned_face_id INTEGER PRIMARY KEY,
      label TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    """)
    con.commit()
    return con


def _safepath(root:Path, relative:str) -> Path | None:
    if (not relative or relative.startswith("/") or "\x00" in relative
        or any(bit in ("",".","..") for bit in relative.split("/"))):
        return None
    p=root/relative
    try:
        if not p.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
            return None
        if p.is_symlink():
            return None
        return p
    except OSError:
        return None


def verify_models(folder:Path=MODEL_ROOT) -> dict[str,int]:
    lengths={}
    for name,digest in MODELS.items():
        f=folder/name
        if f.is_symlink() or not f.is_file():
            raise FileNotFoundError(f"Verified face model absent: {name}")
        actual=hashlib.sha256(f.read_bytes()).hexdigest()
        if actual!=digest:
            raise ValueError(f"model SHA256 mismatch: {name}")
        lengths[name]=f.stat().st_size
    return lengths


def _cpu_session(path:Path):
    import onnxruntime as ort
    options=ort.SessionOptions()
    options.intra_op_num_threads=2
    options.inter_op_num_threads=1
    options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path),sess_options=options,
                                providers=["CPUExecutionProvider"])


def prepare_rgb(path:Path):
    from PIL import Image,ImageOps
    import numpy as np
    with Image.open(path) as source:
        w,h=source.size
        if w<=0 or h<=0 or w*h>MAX_DECODE_PIXELS:
            raise ValueError("too many decode pixels")
        # Avoid huge JPEG allocations; decoder-downscale before EXIF transpose.
        if source.format=="JPEG":
            source.draft("RGB",(1600,1600))
        frame=ImageOps.exif_transpose(source)
        frame.thumbnail((MODEL_WIDTH,MODEL_WIDTH),Image.Resampling.BILINEAR)
        image=frame.convert("RGB")
        canvas=Image.new("RGB",(MODEL_WIDTH,MODEL_WIDTH))
        left=(MODEL_WIDTH-image.width)//2
        top=(MODEL_WIDTH-image.height)//2
        canvas.paste(image,(left,top))
        return np.asarray(canvas,dtype=np.uint8)


def nms(boxes,confidence,threshold=0.32,max_results=16):
    import numpy as np
    if len(confidence)==0:
        return []
    scores=np.asarray(confidence,dtype=np.float32)
    b=np.asarray(boxes,dtype=np.float32)
    order=np.argsort(scores)[::-1]
    keep=[]
    while len(order) and len(keep)<max_results:
        chosen=int(order[0])
        keep.append(chosen)
        if len(order)==1:
            break
        rest=order[1:]
        x1=np.maximum(b[chosen,0],b[rest,0])
        y1=np.maximum(b[chosen,1],b[rest,1])
        x2=np.minimum(b[chosen,0]+b[chosen,2],b[rest,0]+b[rest,2])
        y2=np.minimum(b[chosen,1]+b[chosen,3],b[rest,1]+b[rest,3])
        intersection=np.maximum(0,x2-x1)*np.maximum(0,y2-y1)
        area=b[:,2]*b[:,3]
        iou=intersection/np.maximum(area[chosen]+area[rest]-intersection,1e-9)
        order=rest[iou<threshold]
    return keep


def decode_yunet(output:dict,threshold:float=MIN_FACE_SCORE,
                 width:int=MODEL_WIDTH):
    """OpenCV YuNet anchor-free output decoding, supports strides 8/16/32."""
    import numpy as np
    boxes=[]; keypoints=[]; scores=[]
    for stride in (8,16,32):
        count=width//stride
        cls=output[f"cls_{stride}"].reshape(-1)
        obj=output[f"obj_{stride}"].reshape(-1)
        b=output[f"bbox_{stride}"].reshape(-1,4)
        kp=output[f"kps_{stride}"].reshape(-1,5,2)
        if len(cls)!=count*count or len(obj)!=len(cls) or len(b)!=len(cls):
            raise ValueError("Unexpected YuNet feature map")
        conf=np.sqrt(np.clip(cls,0,1)*np.clip(obj,0,1))
        selected=np.flatnonzero(conf>=threshold)
        if not len(selected):
            continue
        r=selected//count;c=selected%count
        bb=b[selected]
        center=np.column_stack((c+bb[:,0],r+bb[:,1]))*stride
        size=np.exp(np.clip(bb[:,2:4],-8,8))*stride
        coords=np.column_stack((center-size/2,size))
        landmarks=(np.stack((c,r),axis=1)[:,None,:]+kp[selected])*stride
        for i in range(len(selected)):
            if size[i,0]<MIN_FACE_EDGE or size[i,1]<MIN_FACE_EDGE:
                continue
            boxes.append(coords[i]);keypoints.append(landmarks[i]);scores.append(float(conf[selected[i]]))
    keep=nms(boxes,scores)
    return [(boxes[i],keypoints[i],scores[i]) for i in keep]


TEMPLATE = ((38.2946,51.6963),(73.5318,51.5014),(56.0252,71.7366),
            (41.5493,92.3655),(70.7299,92.2041))


def aligned_face(rgb, landmarks):
    """112x112 similarity transform entirely via PIL+NumPy; OpenCV-free."""
    from PIL import Image
    import numpy as np
    pts=np.asarray(landmarks,dtype=np.float64)
    target=np.asarray(TEMPLATE,dtype=np.float64)
    if pts.shape!=(5,2) or not np.isfinite(pts).all():
        raise ValueError("Invalid landmark geometry")
    # Output -> input affine matrix constrained to rotation + scale + translation.
    xy=np.zeros((10,4),dtype=np.float64)
    y=pts.reshape(-1)
    xy[0::2,0]=target[:,0]
    xy[0::2,1]=-target[:,1]
    xy[0::2,2]=1
    xy[1::2,0]=target[:,1]
    xy[1::2,1]=target[:,0]
    xy[1::2,3]=1
    coeff,residual,rank,_=np.linalg.lstsq(xy,y,rcond=None)
    if rank<4 or not np.isfinite(coeff).all():
        raise ValueError("Bad face alignment")
    a,b,tx,ty=coeff.tolist()
    if not .15 < math.hypot(a,b) < 8:
        raise ValueError("Outlier alignment scale")
    im=Image.fromarray(rgb,mode="RGB")
    return im.transform((112,112),Image.Transform.AFFINE,
                        (a,-b,tx,b,a,ty),resample=Image.Resampling.BILINEAR)


def extract_faces(image_path:Path,det_session,rec_session):
    import numpy as np
    rgb=prepare_rgb(image_path)
    # YuNet expects BGR 0..255 float32, NCHW.
    tensor=np.ascontiguousarray(rgb[...,::-1].transpose(2,0,1)[None].astype(np.float32))
    names=[x.name for x in det_session.get_outputs()]
    data=det_session.run(names,{"input":tensor})
    decoded=decode_yunet(dict(zip(names,data)))
    result=[]
    for box,points,score in decoded:
        try:
            crop=aligned_face(rgb,points)
            # OpenCV blobFromImage(...,swapRB=True) produces RGB 0..255, no mean.
            t=np.asarray(crop,dtype=np.float32).transpose(2,0,1)[None]
            embedding=rec_session.run(["fc1"],{"data":np.ascontiguousarray(t)})[0].reshape(-1)
            norm=float(np.linalg.norm(embedding))
            if len(embedding)!=128 or not np.isfinite(norm) or norm<1e-8:
                continue
            embedding=(embedding/norm).astype(np.float32)
            result.append((box,score,embedding))
        except (ValueError,FloatingPointError):
            continue
    return result


def iter_pending(source:Path, dest:sqlite3.Connection, limit:int, root:Path):
    with closing(sqlite_ro(source)) as c:
        cur=c.execute("""SELECT id,path,size,mtime FROM images
                     WHERE path NOT LIKE 'shizuku://%'
                     ORDER BY mtime DESC, id DESC""")
        count=0
        for image_id,relative,size,mtime in cur:
            if count>=limit:
                break
            record=dest.execute("SELECT path,size,mtime,algorithm,status FROM face_scans WHERE image_id=?",
                                (image_id,)).fetchone()
            if record and record[:4]==(relative,size,mtime,VERSION):
                continue
            if not 0 < size <= MAX_FILE_BYTES:
                continue
            path=_safepath(root,relative)
            if path is None:
                continue
            try:
                st=path.stat()
                if not path.is_file() or st.st_size!=size or abs(st.st_mtime-mtime)>3:
                    continue
            except OSError:
                continue
            count+=1
            yield (image_id,relative,int(size),float(mtime),path)


def nonimage_archive_header(path:Path) -> str | None:
    """Reject mislabeled archives before loading third-party image decoders."""
    try:
        with path.open("rb") as src:
            start=src.read(8)
        if start.startswith(b"PK\x03\x04"):
            return "ZIP_SIGNATURE"
        if start.startswith(b"Rar!"):
            return "RAR_SIGNATURE"
        if start.startswith(b"7z\xbc\xaf\x27\x1c"):
            return "SEVENZIP_SIGNATURE"
    except OSError:
        return None
    return None


def reconcile_existing_mislabeled_archives(db:sqlite3.Connection,root:Path,
                                           limit:int=500) -> int:
    """Correct error classification without modifying originals or embeddings."""
    rows=db.execute("""SELECT image_id,path,size,mtime FROM face_scans
                    WHERE status='ERROR' AND error_code='UnidentifiedImageError'
                    LIMIT ?""",(limit,)).fetchall()
    updated=0
    for image_id,relative,size,mtime in rows:
        path=_safepath(root,relative)
        if path is None or not path.is_file():
            continue
        try:
            stat=path.stat()
            if stat.st_size!=size or abs(stat.st_mtime-mtime)>3:
                continue
        except OSError:
            continue
        error=nonimage_archive_header(path)
        if not error:
            continue
        with db:
            db.execute("""UPDATE face_scans SET status='SKIPPED_NONIMAGE',
                  error_code=? WHERE image_id=? AND status='ERROR'
                  AND error_code='UnidentifiedImageError'""",(error,image_id))
        updated+=1
    return updated


def battery_safe(require_charging:bool=False) -> tuple[bool,str]:
    try:
        r=subprocess.run(["termux-battery-status"],capture_output=True,text=True,timeout=5)
        if r.returncode:
            return (not require_charging),"battery telemetry unavailable"
        data=json.loads(r.stdout)
        level=int(data.get("percentage",100))
        status=str(data.get("status","")).upper()
        temperature=float(data.get("temperature",0))
        if require_charging:
            if status not in ("CHARGING","FULL"):
                return False,"charging-required guard"
            if level<30:
                return False,f"charging-required minimum 30%, current {level}%"
            if temperature>=40:
                return False,f"charging-required thermal guard {temperature}C"
        if temperature>=42:
            return False,f"thermal guard {temperature}C"
        # User explicitly opted into indexing at any battery percentage.
        # Keep the device-temperature cutoff; battery 10% is allowed.
        return True,f"battery {level}%, {temperature}C, {status}"
    except (ValueError,OSError,subprocess.TimeoutExpired):
        return (not require_charging),"battery telemetry unavailable"


def do_batch(source:Path, destination:Path, models:Path, root:Path,
             limit:int,max_seconds:int, ignore_battery:bool=False,
             require_charging:bool=False) -> dict:
    if ignore_battery and require_charging:
        raise ValueError("cannot override a mandatory charging guard")
    if require_charging or not ignore_battery:
        proceed,why=battery_safe(require_charging=require_charging)
        if not proceed:
            return {"ok":False,"blocked":why,"scanned":0}
    else:why="battery guard explicitly overridden"
    verify_models(models)
    start=time.monotonic()
    with closing(setup_private_db(destination)) as db:
        recovered=reconcile_existing_mislabeled_archives(db,root)
        det=_cpu_session(models/"face_detection_yunet_2023mar.onnx")
        rec=_cpu_session(models/"face_recognition_sface_2021dec.onnx")
        counts={"processed":0,"images_with_faces":0,"faces":0,"failures":0}
        for image_id,relative,size,mtime,path in iter_pending(source,db,limit,root):
            if counts["processed"]>0 and time.monotonic()-start>max_seconds:
                counts["paused"]="time budget reached"
                break
            period=12 if require_charging else 24
            if (require_charging or not ignore_battery) and counts["processed"] and counts["processed"]%period==0:
                proceed,condition=battery_safe(require_charging=require_charging)
                if not proceed:
                    counts["paused"]=condition
                    break
            status="DONE";error=None;faces=[]
            try:
                nonimage=nonimage_archive_header(path)
                if nonimage:
                    status="SKIPPED_NONIMAGE";error=nonimage
                else:
                    faces=extract_faces(path,det,rec)
            except (OSError,ValueError,RuntimeError,MemoryError) as exc:
                status="ERROR";error=type(exc).__name__;counts["failures"]+=1
            except Exception as exc:
                # Decoder-specific exceptions must not terminate a batch.
                # Never log the full personal photo path.
                if type(exc).__name__ not in ("DecompressionBombError",):
                    raise
                status="ERROR";error=type(exc).__name__;counts["failures"]+=1
            with db:
                db.execute("DELETE FROM face_clusters WHERE face_id IN "
                           "(SELECT face_id FROM face_embeddings WHERE image_id=?)",(image_id,))
                db.execute("DELETE FROM face_embeddings WHERE image_id=?",(image_id,))
                for ord_,(rect,score,vector) in enumerate(faces):
                    db.execute("""INSERT INTO face_embeddings(
                       image_id,face_ordinal,x,y,width,height,score,vector,algorithm)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                       (image_id,ord_,*(float(x) for x in rect),float(score),
                        vector.tobytes(),VERSION))
                db.execute("""INSERT INTO face_scans
                      (image_id,path,size,mtime,algorithm,status,face_count,checked_at,error_code)
                      VALUES(?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(image_id) DO UPDATE SET
                      path=excluded.path,size=excluded.size,mtime=excluded.mtime,
                      algorithm=excluded.algorithm,status=excluded.status,
                      face_count=excluded.face_count,checked_at=excluded.checked_at,
                      error_code=excluded.error_code""",
                      (image_id,relative,size,mtime,VERSION,status,len(faces),now(),error))
            counts["processed"]+=1
            counts["faces"]+=len(faces)
            counts["images_with_faces"]+=bool(faces)
        counts.update({"ok":True,"archive_mislabeled_reclassified":recovered,
                       "elapsed_seconds":round(time.monotonic()-start,2),
                       "models":"YuNet+SFace local verified",
                       "device_policy":why,"photos_modified":False,
                       "images_uploaded":False,"recognition_labels_assigned":False})
        return counts


def group_embeddings(dbpath:Path, threshold:float=STRICT_CLUSTER_COSINE) -> dict:
    """Stable, conservative groups: center shortlisting + complete-link check.

    A new face can join only when it is sufficiently similar to a bounded
    sample of ALL group members. If two groups compete without clear margin,
    make a new (unassigned) group. Never guess a person's name.
    """
    import numpy as np
    if not .4<=threshold<=.95:
        raise ValueError("threshold range")
    with closing(setup_private_db(dbpath)) as db:
        rows=db.execute("SELECT face_id,image_id,vector FROM face_embeddings ORDER BY face_id").fetchall()
        groups=[]
        centers=[]
        image_ids_by_group=[]
        limit_anchors=40
        for face_id,image_id,raw in rows:
            vec=np.frombuffer(raw,dtype=np.float32)
            if len(vec)!=128 or not np.isfinite(vec).all():
                continue
            if groups:
                matrix=np.vstack(centers)
                similarities=matrix@vec
                contenders=np.flatnonzero(similarities>=threshold)
                ranked=sorted((int(x) for x in contenders),
                              key=lambda x:float(similarities[x]),reverse=True)
                viable=[]
                for idx in ranked:
                    # Two separately detected faces in the SAME photograph
                    # cannot be inferred to be one person's face.
                    if image_id in image_ids_by_group[idx]:
                        continue
                    # Sample both old and newly joined members: using only
                    # the first 40 can overlook new cluster drift.
                    existing=groups[idx]
                    if len(existing)>limit_anchors:
                        sample=np.linspace(0,len(existing)-1,num=limit_anchors,dtype=int)
                        references=[existing[int(j)] for j in sample]
                    else:
                        references=existing
                    members=np.vstack([x[1] for x in references])
                    pairwise=members@vec
                    if float(pairwise.min())>=threshold:
                        viable.append((float(pairwise.mean()),idx))
                    if len(viable)>=2:
                        break
                viable.sort(reverse=True)
                if viable and (len(viable)==1 or viable[0][0]-viable[1][0]>=.06):
                    i=viable[0][1]
                    groups[i].append((face_id,vec))
                    image_ids_by_group[i].add(image_id)
                    # Weight once per group, using newest vector.
                    center=(centers[i]*(len(groups[i])-1)+vec)/len(groups[i])
                    norm=np.linalg.norm(center)
                    if norm>1e-10:
                        centers[i]=(center/norm).astype(np.float32)
                    continue
            groups.append([(face_id,vec)])
            centers.append(vec.copy())
            image_ids_by_group.append({image_id})
        with db:
            db.execute("DELETE FROM face_clusters")
            for gid,items in enumerate(groups,1):
                db.executemany("INSERT INTO face_clusters(face_id,cluster_id,assigned_at)"
                               " VALUES(?,?,?)",
                               [(int(fid),gid,now()) for fid,_ in items])
        sizes=[len(x) for x in groups]
        return {"ok":True,"faces":sum(sizes),"groups":len(groups),
                "groups_with_2plus":sum(x>=2 for x in sizes),
                "threshold":threshold,"manual_names_unchanged":True,
                "automatic_person_names":False,
                "same_image_faces_merged":False}


def name_group(dbpath:Path,cluster_id:int,name:str,
               expected_revision:str|None=None) -> dict:
    """Pin explicitly confirmed name only if this is the same face group."""
    if not (isinstance(name,str) and 1<=len(name.strip())<=60
            and not any(ord(c)<32 for c in name)):
        raise ValueError("Invalid group name")
    if not isinstance(cluster_id,int) or isinstance(cluster_id,bool) or cluster_id<=0:
        raise ValueError("Invalid group")
    with closing(setup_private_db(dbpath)) as db:
        # Hold the SQLite writer lock for the revision check AND the write:
        # a concurrent regroup cannot swap a group's identity in between.
        db.execute("BEGIN IMMEDIATE")
        try:
            revision=group_revision(db,cluster_id)
            if expected_revision is not None and expected_revision!=revision:
                raise ValueError("face group changed; reload before naming")
            row=db.execute("""SELECT e.face_id FROM face_embeddings e
               JOIN face_clusters g ON g.face_id=e.face_id
               WHERE g.cluster_id=? ORDER BY e.score DESC LIMIT 1""",
                       (cluster_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown face group")
            db.execute("""INSERT INTO face_manual_names(pinned_face_id,label,created_at)
               VALUES(?,?,?)
               ON CONFLICT(pinned_face_id) DO UPDATE SET label=excluded.label""",
               (row[0],name.strip(),now()))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"ok":True,"group_id":cluster_id,"name":name.strip(),
                "revision":revision,"manually_confirmed":True,
                "person_identified_automatically":False}


def group_revision(db:sqlite3.Connection, cluster_id:int) -> str:
    """Membership-bound snapshot ID prevents stale page actions after regroup."""
    face_ids=[str(int(row[0])) for row in db.execute(
        "SELECT face_id FROM face_clusters WHERE cluster_id=? ORDER BY face_id",
        (cluster_id,))]
    if not face_ids:
        raise ValueError("unknown face group")
    return hashlib.sha256(
        (str(cluster_id)+":"+(",".join(face_ids))).encode("ascii")
    ).hexdigest()[:24]


def group_photo_page(dbpath:Path, cluster_id:int, offset:int=0,
                     limit:int=48,revision:str|None=None) -> dict:
    """Bounded read-only pagination for all photos of one anonymous face group."""
    if (not isinstance(cluster_id,int) or isinstance(cluster_id,bool)
        or cluster_id<=0
        or not isinstance(offset,int) or isinstance(offset,bool)
        or not 0<=offset<=100000
        or not isinstance(limit,int) or isinstance(limit,bool)
        or not 1<=limit<=80):
        raise ValueError("invalid face group photo page")
    with closing(sqlite_ro(dbpath)) as c:
        live_revision=group_revision(c,cluster_id)
        if revision is not None and revision!=live_revision:
            raise ValueError("face group has changed; reload")
        row=c.execute("""SELECT COUNT(DISTINCT e.image_id)
                         FROM face_embeddings e JOIN face_clusters g
                         ON g.face_id=e.face_id WHERE g.cluster_id=?""",
                      (cluster_id,)).fetchone()
        total=int(row[0]) if row else 0
        if total<=0:
            raise ValueError("unknown face group")
        result=c.execute("""SELECT e.image_id,MAX(e.score) AS best
                            FROM face_embeddings e JOIN face_clusters g
                            ON g.face_id=e.face_id
                            WHERE g.cluster_id=?
                            GROUP BY e.image_id
                            ORDER BY best DESC,e.image_id
                            LIMIT ? OFFSET ?""",
                         (cluster_id,limit,offset)).fetchall()
    return {"ok":True,"group_id":cluster_id,"revision":live_revision,"total":total,
            "offset":offset,"limit":limit,
            "photo_ids":[int(row[0]) for row in result],
            "has_more":offset+len(result)<total,
            "identity_inferred":False}


def groups_report(dbpath:Path, source:Path, limit:int=50000) -> dict:
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=50000:
        raise ValueError("invalid groups report limit")
    with closing(sqlite_ro(dbpath)) as c,closing(sqlite_ro(source)) as gallery:
        total_groups=int(c.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM face_clusters"
        ).fetchone()[0])
        groups=c.execute("""
        SELECT cluster_id, COUNT(DISTINCT image_id)
        FROM face_clusters g JOIN face_embeddings e ON g.face_id=e.face_id
        GROUP BY cluster_id ORDER BY COUNT(DISTINCT image_id) DESC,cluster_id LIMIT ?""",
        (limit,)).fetchall()
        out=[]
        for cluster_id,n in groups:
            names=c.execute("""SELECT n.label FROM face_manual_names n
                  JOIN face_clusters g ON g.face_id=n.pinned_face_id
                  WHERE g.cluster_id=? ORDER BY n.created_at DESC LIMIT 1""",
                  (cluster_id,)).fetchone()
            rows=c.execute("""SELECT e.face_id,e.image_id FROM face_embeddings e
                  JOIN face_clusters g ON e.face_id=g.face_id
                  WHERE g.cluster_id=? ORDER BY e.score DESC LIMIT 30""",
                  (cluster_id,)).fetchall()
            out.append({"id":cluster_id,"revision":group_revision(c,cluster_id),
                        "photos":n,
                        "name":names[0] if names else f"Человек {cluster_id}",
                        "verified_name":bool(names),
                        "photo_ids":list(dict.fromkeys(image for _,image in rows))[:18]})
        return {"ok":True,"groups":out,
                "total_groups":total_groups,
                "groups_truncated":len(out)<total_groups,
                "indexed_faces":c.execute("SELECT count(*) FROM face_embeddings").fetchone()[0],
                "scanned_images":c.execute("SELECT count(*) FROM face_scans WHERE status='DONE'").fetchone()[0],
                "photos_modified":False,"cloud_upload":False}


def attach_face_covers(report:dict, faces_db:Path, gallery_db:Path,
                       root:Path=SHARED_ROOT,max_groups:int=100) -> dict:
    """Embed small, derived face crops only into a private local preview.

    Sensitive portrait bytes are NEVER uploaded and never stored in SQL.
    """
    import base64
    import io
    from PIL import Image
    out=dict(report)
    groups=[dict(x) for x in report["groups"]]
    out["groups"]=groups
    total_encoded=0
    with closing(sqlite_ro(faces_db)) as faces,closing(sqlite_ro(gallery_db)) as gallery:
        for group in groups[:max_groups]:
            rows=faces.execute("""SELECT e.image_id,e.x,e.y,e.width,e.height
                  FROM face_embeddings e JOIN face_clusters g ON g.face_id=e.face_id
                  WHERE g.cluster_id=? ORDER BY e.score DESC LIMIT 4""",
                  (group["id"],)).fetchall()
            for image_id,x,y,w,h in rows:
                record=gallery.execute("""SELECT path,size,mtime FROM images
                               WHERE id=? AND path NOT LIKE 'shizuku://%'""",
                               (image_id,)).fetchone()
                if not record:
                    continue
                rel,size,mtime=record
                source=_safepath(root,rel)
                try:
                    if source is None or source.is_symlink():
                        continue
                    st=source.stat()
                    if not source.is_file() or st.st_size!=size or abs(st.st_mtime-mtime)>3:
                        continue
                    im=Image.fromarray(prepare_rgb(source))
                    left=max(0,int(x-.15*w))
                    top=max(0,int(y-.15*h))
                    right=min(MODEL_WIDTH,int(x+1.15*w))
                    bottom=min(MODEL_WIDTH,int(y+1.15*h))
                    if right-left<20 or bottom-top<20:
                        continue
                    face=im.crop((left,top,right,bottom))
                    face.thumbnail((128,128),Image.Resampling.BILINEAR)
                    buf=io.BytesIO()
                    face.save(buf,format="JPEG",quality=65,optimize=True)
                    image_bytes=buf.getvalue()
                    if len(image_bytes)>30_000:
                        continue
                    content=base64.b64encode(image_bytes).decode("ascii")
                    if total_encoded+len(content)>6_000_000:
                        break
                    group["face_cover_data_uri"]="data:image/jpeg;base64,"+content
                    total_encoded+=len(content)
                    break
                except (OSError,ValueError,MemoryError):
                    continue
    out["cover_count"]=sum(bool(x.get("face_cover_data_uri")) for x in groups)
    out["cover_bytes_embedded"]=total_encoded
    return out


def render_private_gallery(report:dict, live:bool=False) -> str:
    html=["<!doctype html><html lang='ru'><meta charset='utf-8'>",
          "<meta name='viewport' content='width=device-width,initial-scale=1'>",
          "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; img-src http://127.0.0.1:18780 data:; style-src 'unsafe-inline'; "+
          ("connect-src 'self'; script-src 'unsafe-inline'; " if live else "")+"\">",
          "<title>Люди — приватная галерея</title><style>body{font-family:system-ui;background:#14171d;color:#eee;padding:12px}.groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}section{border:1px solid #33414d;padding:10px;border-radius:12px}img{width:75px;height:75px;object-fit:cover;border-radius:7px;margin:2px;background:#000}.portrait{width:110px;height:110px;object-fit:cover;float:right;border:2px solid #698394}button{border:1px solid #708194;background:#223444;color:#fff;padding:8px;border-radius:7px}</style>",
          "<h2>Люди на фотографиях</h2>",
          "<p>Автоматическая группировка по лицам. Возможны неточные объединения и разделения. Имена вводишь только ты — система не определяет личность самостоятельно.</p>",
          f"<p>Обработано фото: {report['scanned_images']}; найдено лиц: {report['indexed_faces']}; групп: {len(report['groups'])}"+(" (показана часть)." if report.get("groups_truncated",False) else " (показаны все).")+"</p><p id='state' role='status'></p>"]
    if live:
        html.append(
            "<div><label>Поиск по группам "
            "<input id='personFilter' type='search' oninput='filterPeople()'></label> "
            "<label><input type='checkbox' id='multiOnly' onchange='filterPeople()'>"
            " Только 2+ фото</label> "
            "<span id='filteredCount'></span></div>")
    html.append("<main class='groups'>")
    for group in report["groups"]:
        gid=int(group["id"])
        name=escape(group["name"])
        revision=str(group.get("revision",""))
        html.append(
            f"<section data-group='{gid}' data-revision='{revision}' "
            f"data-photo-count='{int(group['photos'])}'>")
        cover=group.get("face_cover_data_uri")
        if cover and cover.startswith("data:image/jpeg;base64,"):
            html.append(
                f'<img class="portrait" loading="lazy" decoding="async" '
                f'alt="Лицо из группы {gid}" src="{cover}">')
        else:
            # CPU-intensive crops limited to 100; other groups previously
            # lacked a cover. Reuse a lazy local photo thumbnail.
            ids=group.get("photo_ids") or []
            if ids and isinstance(ids[0],int) and not isinstance(ids[0],bool) and ids[0]>0:
                html.append(
                    f'<img class="portrait" loading="lazy" decoding="async" '
                    f'alt="Фото группы {gid}" '
                    f'src="http://127.0.0.1:18780/api/thumb?id={ids[0]}">')
        html.append(f'<h3 id="g{gid}">{name}</h3>')
        html.append(f'<p>{group["photos"]} фото с лицом</p>')
        if live:
            html.append(f'<button onclick="renameGroup({gid})">Назвать группу</button>')
        if live:
            # Do not pre-render only the first 18: fetch any group's full
            # gallery on demand, in bounded pages with lazy thumbnails.
            html.append(
                f'<details data-group-id="{gid}" data-revision="{revision}" ontoggle="onGroupToggle(this)">'
                '<summary>Показать все фотографии</summary>'
                '<div class="photos"></div>'
                '<p class="page-status" aria-live="polite"></p>'
                '<button type="button" class="load-more"'
                ' onclick="loadNextGroupPage(this.closest(\'details\'))"'
                ' hidden>Загрузить ещё</button></details>'
            )
        else:
            html.append("<details><summary>Первые фотографии (оффлайн)</summary>")
            for image_id in group["photo_ids"]:
                html.append(f'<img loading="lazy" alt="Фото {int(image_id)}" src="http://127.0.0.1:18780/api/thumb?id={int(image_id)}">')
            if group["photos"]>len(group["photo_ids"]):
                html.append(f"<p>В этом сохранённом отчёте показаны только первые {len(group['photo_ids'])} из {group['photos']}. Для всех снимков открой интерактивный просмотр.</p>")
            html.append("</details>")
        html.append("</section>")
    html.append("</main>")
    if live:
        html.append("""<script>
function filterPeople(){
 const value=document.getElementById('personFilter').value.toLocaleLowerCase().trim();
 let count=0;
 document.querySelectorAll('.groups section').forEach(section=>{
  const id=section.dataset.group;
  const title=section.querySelector('h3').textContent.toLocaleLowerCase();
  const multi=document.getElementById('multiOnly').checked;
  const match=(!multi||Number(section.dataset.photoCount)>=2) &&
    (!value||title.includes(value)||id.includes(value));
  section.hidden=!match;
  if(match)count++;
 });
 document.getElementById('filteredCount').textContent=count+' групп показано';
}
window.addEventListener('DOMContentLoaded',filterPeople);
const photoPages = new Map();
function onGroupToggle(details) {
  if (details.open && !photoPages.has(details.dataset.groupId))
    loadNextGroupPage(details);
}
async function loadNextGroupPage(details) {
  if (!details || !details.open) return;
  const key=details.dataset.groupId;
  const status=details.querySelector('.page-status');
  const more=details.querySelector('.load-more');
  const previous=photoPages.get(key) || {offset:0, loading:false, finished:false};
  if(previous.loading || previous.finished) return;
  previous.loading=true;
  photoPages.set(key,previous);
  status.textContent='Загружаю фотографии…';
  more.hidden=true;
  try {
    const prefix=location.pathname.endsWith('/') ?
      location.pathname.slice(0,-1) : location.pathname;
    const url=prefix+'/api/group?id='+encodeURIComponent(key)+
      '&offset='+previous.offset+'&limit=48&revision='+
      encodeURIComponent(details.dataset.revision);
    const resp=await fetch(url,{mode:'same-origin',credentials:'omit',
                                cache:'no-store'});
    const result=await resp.json();
    if(!resp.ok || !result.ok || result.group_id!==Number(key) ||
       result.revision!==details.dataset.revision ||
       result.offset!==previous.offset || !Array.isArray(result.photo_ids))
      throw new Error('invalid gallery page');
    const holder=details.querySelector('.photos');
    for(const value of result.photo_ids) {
      if(!Number.isSafeInteger(value) || value<1)continue;
      const image=document.createElement('img');
      image.loading='lazy';image.alt='Фото '+value;
      image.src='http://127.0.0.1:18780/api/thumb?id='+value;
      holder.appendChild(image);
    }
    previous.offset+=result.photo_ids.length;
    previous.finished=!result.has_more;
    status.textContent='Показано '+previous.offset+' из '+result.total+
      ' фотографий';
    more.hidden=previous.finished;
  } catch (_) {
    status.textContent='Группа могла измениться. Обнови страницу, если снимки не загружаются.';
    more.hidden=false;
  } finally {
    previous.loading=false;
  }
}
async function renameGroup(id){
 const current=document.getElementById('g'+id).textContent;
 const name=prompt('Имя человека (только по твоему подтверждению):',current);
 if(name===null)return;
 if(!name.trim()||name.length>60){alert('Имя: 1–60 символов');return;}
 const path=location.pathname.endsWith('/')?location.pathname.slice(0,-1):location.pathname;
 try{
  const resp=await fetch(path+'/api/name',{method:'POST',mode:'same-origin',
   credentials:'omit',headers:{'Content-Type':'application/json',
   'X-Face-Groups':'manual-user-gesture'},body:JSON.stringify({
     group_id:id,name:name.trim(),
     revision:document.querySelector('[data-group="'+id+'"]')?.dataset.revision
   })});
  const data=await resp.json();
  if(!resp.ok||!data.ok)throw Error('failed');
  document.getElementById('g'+id).textContent=data.name;
  filterPeople();
  document.getElementById('state').textContent='✓ Имя сохранено на телефоне';
 }catch(e){document.getElementById('state').textContent='⚠ Имя не сохранено';}
}
</script>""")
    html.append("</html>")
    return "".join(html)


def status_report(gallery_db:Path,faces_db:Path) -> dict:
    """Exact current local coverage; no SQLite writes or biometric export."""
    with closing(sqlite_ro(gallery_db)) as gallery:
        source={r[0]:(r[1],r[2],r[3]) for r in gallery.execute(
            "SELECT id,path,size,mtime FROM images WHERE path NOT LIKE 'shizuku://%'")}
    if not faces_db.is_file():
        return {"ok":True,"local_images":len(source),"currently_scanned":0,
                "remaining":len(source),"face_instances":0,"face_groups":0,
                "unreadable_files":0,"photos_modified":False,
                "person_names_inferred":False}
    with closing(sqlite_ro(faces_db)) as c:
        scans=c.execute("SELECT image_id,path,size,mtime,status FROM face_scans").fetchall()
        done=sum(1 for id,path,size,mtime,status in scans
                 if id in source and source[id]==(path,size,mtime))
        return {"ok":True,
                "local_images":len(source),
                "currently_scanned":done,
                "remaining":max(0,len(source)-done),
                "face_instances":c.execute("SELECT count(*) FROM face_embeddings").fetchone()[0],
                "face_groups":c.execute("SELECT count(distinct cluster_id) FROM face_clusters").fetchone()[0],
                "unreadable_files":c.execute("SELECT count(*) FROM face_scans WHERE status='ERROR'").fetchone()[0],
                "mislabeled_archives_skipped":c.execute("SELECT count(*) FROM face_scans WHERE status='SKIPPED_NONIMAGE'").fetchone()[0],
                "database_integrity":c.execute("PRAGMA quick_check").fetchone()[0],
                "photos_modified":False,
                "person_names_inferred":False}


def write_private_gallery(report:dict, folder:Path) -> Path:
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    folder.chmod(0o700)
    dest=folder/("face-groups-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".html")
    with os.fdopen(os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"w") as f:
        f.write(render_private_gallery(report,live=False))
    return dest


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--index",action="store_true")
    p.add_argument("--group",action="store_true")
    p.add_argument("--report",action="store_true")
    p.add_argument("--name-group",type=int,default=0,
                   help="Manually assign a name to confirmed group ID")
    p.add_argument("--name",default="",
                   help="Explicitly supplied display name; never auto-inferred")
    p.add_argument("--batch-size",type=int,default=24)
    p.add_argument("--max-seconds",type=int,default=90)
    p.add_argument("--threshold",type=float,default=STRICT_CLUSTER_COSINE)
    p.add_argument("--gallery-db",type=Path,default=STATE/"gallery.sqlite3")
    p.add_argument("--faces-db",type=Path,default=STATE/"face_groups.sqlite3")
    p.add_argument("--shared-root",type=Path,default=SHARED_ROOT)
    p.add_argument("--models",type=Path,default=MODEL_ROOT)
    p.add_argument("--ignore-battery",action="store_true")
    p.add_argument("--require-charging",action="store_true",
                   help="Fail closed if power is unplugged, too low or too hot")
    p.add_argument("--status",action="store_true",
                   help="Read-only current scan progress and group count")
    p.add_argument("--serve",action="store_true",
                   help="Private interactive face groups browser on localhost")
    p.add_argument("--port",type=int,default=0,
                   help="Port for --serve (0 means selected by system)")
    args=p.parse_args()
    if not 1<=args.batch_size<=500 or not 5<=args.max_seconds<=3600:
        p.error("batch-size or max-seconds out of safe range")
    if args.ignore_battery and args.require_charging:
        p.error("--ignore-battery cannot be combined with --require-charging")
    if args.status:
        print(json.dumps(status_report(args.gallery_db,args.faces_db),ensure_ascii=False))
    if args.index:
        print(json.dumps(do_batch(args.gallery_db,args.faces_db,args.models,
                                  args.shared_root,args.batch_size,
                                  args.max_seconds,args.ignore_battery,
                                  args.require_charging),ensure_ascii=False))
    if args.group:
        if args.require_charging:
            allowed,reason=battery_safe(require_charging=True)
            if not allowed:
                print(json.dumps({"ok":False,"blocked":reason,
                                  "groups_recomputed":False},ensure_ascii=False))
                return 0
        print(json.dumps(group_embeddings(args.faces_db,args.threshold),ensure_ascii=False))
    if args.name_group:
        print(json.dumps(name_group(args.faces_db,args.name_group,args.name),
                         ensure_ascii=False))
    if args.report:
        info=groups_report(args.faces_db,args.gallery_db)
        info=attach_face_covers(info,args.faces_db,args.gallery_db,args.shared_root)
        name=write_private_gallery(info,args.faces_db.parent/"forensics")
        print(json.dumps({"ok":True,"gallery_html":str(name),
                          "groups_shown":len(info["groups"]),
                          "faces":info["indexed_faces"],"scanned":info["scanned_images"]},
                         ensure_ascii=False))
    if args.serve:
        from gallery_face_web import serve
        info=groups_report(args.faces_db,args.gallery_db)
        info=attach_face_covers(info,args.faces_db,args.gallery_db,args.shared_root)
        return serve(info,args.faces_db,args.port)
    if not(args.index or args.group or args.report or args.name_group or args.serve or args.status):
        p.print_help()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
