"""Local CLIP semantic image/text embeddings via ONNX Runtime.
Author: @dvkrum
"""
from __future__ import annotations
import json, math, sqlite3, time, unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any
from photo_config import HOME
import gallery_shizuku
MODEL_ID="Xenova/clip-vit-base-patch32-quantized"
MODEL_DIR=HOME/"models"/"clip-vit-base-patch32"
VISION=MODEL_DIR/"vision_model_quantized.onnx"; TEXT=MODEL_DIR/"text_model_quantized.onnx"; VOCAB=MODEL_DIR/"vocab.json"; MERGES=MODEL_DIR/"merges.txt"
DIM=512

def _bytes_to_unicode():
    bs=list(range(ord("!"),ord("~")+1))+list(range(ord("¡"),ord("¬")+1))+list(range(ord("®"),ord("ÿ")+1)); cs=bs[:]; n=0
    for b in range(256):
        if b not in bs: bs.append(b); cs.append(256+n); n+=1
    return dict(zip(bs,[chr(n) for n in cs]))
BYTE_ENCODER=_bytes_to_unicode()

def _pairs(word): return set(zip(word,word[1:]))

class ClipTokenizer:
    def __init__(self):
        self.encoder=json.loads(VOCAB.read_text(encoding="utf-8")); lines=MERGES.read_text(encoding="utf-8").splitlines()[1:49152-256-2+1]
        self.ranks={tuple(line.split()):i for i,line in enumerate(lines) if line.strip()}; self.cache={"<|startoftext|>":"<|startoftext|>","<|endoftext|>":"<|endoftext|>"}
        self.sot=self.encoder["<|startoftext|>"]; self.eot=self.encoder["<|endoftext|>"]
    def _split(self,text):
        text=" ".join(text.lower().strip().split()); out=[]; i=0
        contractions=("'s","'t","'re","'ve","'m","'ll","'d")
        while i<len(text):
            if text[i].isspace(): i+=1; continue
            matched=next((suffix for suffix in contractions if text.startswith(suffix,i)),None)
            if matched:
                out.append(matched); i+=len(matched); continue
            c=text[i]; cat=unicodedata.category(c)
            if cat.startswith("L"):
                j=i+1
                while j<len(text) and unicodedata.category(text[j]).startswith("L"): j+=1
                out.append(text[i:j]); i=j
            elif cat.startswith("N"):
                out.append(c); i+=1
            else:
                j=i+1
                while j<len(text) and (not text[j].isspace()) and not unicodedata.category(text[j]).startswith(("L","N")): j+=1
                out.append(text[i:j]); i=j
        return out
    def bpe(self,token):
        if token in self.cache:return self.cache[token]
        word=tuple(token[:-1])+ (token[-1]+"</w>",) if token else tuple(); pairs=_pairs(word)
        if not pairs:return token+"</w>"
        while True:
            bigram=min(pairs,key=lambda p:self.ranks.get(p,float("inf")))
            if bigram not in self.ranks:break
            first,second=bigram; new=[]; i=0
            while i<len(word):
                try:j=word.index(first,i)
                except ValueError:new.extend(word[i:]);break
                new.extend(word[i:j]); i=j
                if i<len(word)-1 and word[i]==first and word[i+1]==second:new.append(first+second);i+=2
                else:new.append(word[i]);i+=1
            word=tuple(new)
            if len(word)==1:break
            pairs=_pairs(word)
        result=" ".join(word); self.cache[token]=result; return result
    def encode(self,text):
        ids=[self.sot]
        for token in self._split(text):
            encoded="".join(BYTE_ENCODER[b] for b in token.encode("utf-8"))
            ids.extend(self.encoder[piece] for piece in self.bpe(encoded).split(" "))
        ids=ids[:75]+[self.eot]; ids+= [0]*(77-len(ids)); return ids

@lru_cache(maxsize=1)
def _runtime():
    if not all(p.is_file() for p in (VISION,TEXT,VOCAB,MERGES)): raise RuntimeError("semantic model not installed")
    import numpy as np, onnxruntime as ort
    opts=ort.SessionOptions(); opts.intra_op_num_threads=2; opts.inter_op_num_threads=1
    providers=["CPUExecutionProvider"]
    return np,ort.InferenceSession(str(VISION),sess_options=opts,providers=providers),ort.InferenceSession(str(TEXT),sess_options=opts,providers=providers),ClipTokenizer()

def available():
    try:_runtime();return True
    except Exception:return False

def _pick_embedding(outputs):
    for arr in outputs:
        if getattr(arr,"ndim",0)==2 and arr.shape[-1]==DIM:return arr
    raise RuntimeError("CLIP embedding output missing")

def _normalize(np,v):
    v=v.astype("float32"); norm=np.linalg.norm(v,axis=-1,keepdims=True); return v/np.maximum(norm,1e-12)

def text_embedding(text):
    np,vision,session,tok=_runtime(); ids=np.asarray([tok.encode(text)],dtype=np.int64); inputs={}
    for inp in session.get_inputs():
        if inp.name=="input_ids":inputs[inp.name]=ids
        elif inp.name=="attention_mask":inputs[inp.name]=(ids!=0).astype(np.int64)
        else: raise RuntimeError("unsupported CLIP text input")
    return _normalize(np,_pick_embedding(session.run(None,inputs)))[0]

def path_embedding(g,path):
    """Create a normalized CLIP embedding from one local image path."""
    np,session,text,tok=_runtime()
    with g["Image"].open(path) as src:
        img=g["ImageOps"].exif_transpose(src).convert("RGB")
        w,h=img.size; scale=224/min(w,h); nw=max(224,round(w*scale)); nh=max(224,round(h*scale)); img=img.resize((nw,nh),g["Image"].Resampling.BICUBIC)
        left=(nw-224)//2; top=(nh-224)//2; img=img.crop((left,top,left+224,top+224))
        arr=np.asarray(img,dtype=np.float32)/255.0
    mean=np.asarray([0.48145466,0.4578275,0.40821073],dtype=np.float32); std=np.asarray([0.26862954,0.26130258,0.27577711],dtype=np.float32)
    arr=((arr-mean)/std).transpose(2,0,1)[None,:,:,:]
    inputs={session.get_inputs()[0].name:arr}; return _normalize(np,_pick_embedding(session.run(None,inputs)))[0]

def image_embedding(g,row):
    with g["_materialize_row"](row) as path:
        return path_embedding(g,path)

def install(g: dict[str,Any]) -> None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"]
    def ensure(c):
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_semantic(image_id INTEGER PRIMARY KEY,size INTEGER NOT NULL,mtime REAL NOT NULL,model TEXT NOT NULL,processed_at REAL NOT NULL,embedding BLOB,error TEXT,FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
    def status(_):
        with db() as c:
            ensure(c)
            total=c.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            sh_total=c.execute("SELECT COUNT(*) FROM images WHERE path LIKE 'shizuku://%'").fetchone()[0]
            done=c.execute("SELECT COUNT(*) FROM gallery_semantic s JOIN images i ON i.id=s.image_id WHERE s.size=i.size AND s.mtime=i.mtime AND s.model=?",(MODEL_ID,)).fetchone()[0]
            sh_done=c.execute("SELECT COUNT(*) FROM gallery_semantic s JOIN images i ON i.id=s.image_id WHERE s.size=i.size AND s.mtime=i.mtime AND s.model=? AND i.path LIKE 'shizuku://%'",(MODEL_ID,)).fetchone()[0]
        local_total=total-sh_total; local_done=done-sh_done; sh_available=gallery_shizuku.available_cached(ttl=30.0)
        return {"ok":True,"available":available(),"model":MODEL_ID,"total":total,"indexed":done,"remaining":max(0,total-done),"percent":round(done*100/total,2) if total else 100.0,
                "local":{"total":local_total,"indexed":local_done,"remaining":max(0,local_total-local_done)},
                "android_data":{"total":sh_total,"indexed":sh_done,"remaining":max(0,sh_total-sh_done),"available":sh_available}}
    def index(args):
        if not available():return {"ok":False,"error":"semantic model unavailable","model":MODEL_ID}
        limit=max(1,min(int(args.get("limit",4)),32)); ids=args.get("ids"); deferred=set()
        shizuku_available=True if ids else gallery_shizuku.available_cached(ttl=30.0)
        with db() as c:
            ensure(c)
            if ids:
                rows=[c.execute("SELECT * FROM images WHERE id=?",(int(i),)).fetchone() for i in ids[:32]];rows=[r for r in rows if r]
            else:
                sql="""SELECT i.* FROM images i LEFT JOIN gallery_semantic s ON s.image_id=i.id
                       WHERE (s.image_id IS NULL OR s.size<>i.size OR s.mtime<>i.mtime OR s.model<>?)"""
                params=[MODEL_ID]
                if not shizuku_available:
                    sql += " AND i.path NOT LIKE 'shizuku://%'"; deferred.add("android_data_shizuku")
                sql += """ ORDER BY CASE
                    WHEN lower(i.name) LIKE '%sticker%' OR lower(i.path) LIKE '%/cache/%' OR lower(i.path) LIKE '%/stickers/%' THEN 3
                    WHEN lower(i.album) LIKE '%dcim/camera%' OR lower(i.name) LIKE 'img_%' OR lower(i.name) LIKE '10000%' THEN 0
                    WHEN lower(i.name) LIKE '%screenshot%' THEN 1
                    ELSE 2 END, i.mtime DESC LIMIT ?"""; params.append(limit)
                rows=c.execute(sql,params).fetchall()
        ok=[];errors=[]
        for row in rows:
            if "android_data_shizuku" in deferred and gallery_shizuku.is_virtual(str(row["path"])): continue
            try:
                emb=image_embedding(g,row); g["_validate_row"](row)
                with db() as c:
                    ensure(c);c.execute("""INSERT INTO gallery_semantic(image_id,size,mtime,model,processed_at,embedding,error) VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(image_id) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,model=excluded.model,processed_at=excluded.processed_at,embedding=excluded.embedding,error=NULL""",(row["id"],row["size"],row["mtime"],MODEL_ID,time.time(),emb.astype("float32").tobytes()))
                ok.append(int(row["id"]))
            except gallery_shizuku.ShizukuUnavailable:
                deferred.add("android_data_shizuku"); shizuku_available=False
            except Exception as exc:errors.append({"id":int(row["id"]),"error":type(exc).__name__})
        return {"ok":True,"indexed_ids":ok,"errors":errors,"model":MODEL_ID,"source_photos_modified":False,
                "deferred_sources":sorted(deferred),"shizuku_available":shizuku_available}
    def search(args):
        if not available():return {"ok":False,"error":"semantic model unavailable","items":[]}
        query=str(args.get("query","")).strip()
        if not query:raise ValueError("query is required")
        limit=max(1,min(int(args.get("limit",50)),200)); np,_,_,_=_runtime(); q=text_embedding(query)
        with db() as c:
            ensure(c);rows=c.execute("""SELECT i.*,s.embedding FROM gallery_semantic s JOIN images i ON i.id=s.image_id WHERE s.size=i.size AND s.mtime=i.mtime AND s.model=? AND s.error IS NULL AND s.embedding IS NOT NULL""",(MODEL_ID,)).fetchall()
        scored=[]
        for r in rows:
            v=np.frombuffer(r["embedding"],dtype=np.float32)
            if v.size!=DIM:continue
            score=float(np.dot(q,v));scored.append((score,r))
        scored.sort(key=lambda x:x[0],reverse=True)
        return {"ok":True,"query":query,"model":MODEL_ID,"searched":len(scored),"items":[{**g["_item"](r),"semantic_score":round(score,5)} for score,r in scored[:limit]]}
    tools["gallery_semantic_status"]=(schema(),status); tools["gallery_semantic_index"]=(schema({"ids":{"type":"array","items":{"type":"integer","minimum":1},"maxItems":32},"limit":{"type":"integer","minimum":1,"maximum":32,"default":4}}),index);tools["gallery_semantic_search"]=(schema({"query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":200,"default":50}},["query"]),search)
    descriptions.update({"gallery_semantic_status":"Read local CLIP semantic-index status.","gallery_semantic_index":"Build local CLIP image embeddings using ONNX Runtime.","gallery_semantic_search":"Find photos by natural-language visual meaning using local CLIP embeddings."});readonly.update({"gallery_semantic_status","gallery_semantic_search"})
