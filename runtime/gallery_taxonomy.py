"""Local zero-shot visual taxonomy over existing CLIP embeddings.
Author: @dvkrum
No biometric identity inference is performed.
"""
from __future__ import annotations
import json,time
from array import array
from functools import lru_cache
from typing import Any
from gallery_semantic import MODEL_ID,DIM,text_embedding,available as semantic_available

VERSION="0.6.0"
LABELS={
 "document":"a photo of a document or paper",
 "receipt":"a photo of a receipt or invoice",
 "screenshot":"a screenshot of a phone or computer screen",
 "clothing":"a photo of clothing, shoes, or an outfit",
 "food":"a photo of food or a meal",
 "drink":"a photo of a drink or beverage",
 "animal":"a photo of an animal or pet",
 "person":"a photo containing a person",
 "people":"a photo containing a group of people",
 "room":"a photo of an indoor room",
 "building":"a photo of a building or architecture",
 "vehicle":"a photo of a car, bus, train, bicycle, or other vehicle",
 "landscape":"a landscape or outdoor nature photo",
 "product":"a product photo",
 "package":"a photo of a parcel, box, or package",
 "electronics":"a photo of electronics or a gadget",
 "book":"a photo of a book or printed publication",
 "artwork":"a photo of art, illustration, or drawing",
 "meme":"an internet meme or humorous image",
 "chart":"a chart, graph, table, or diagram",
 "plant":"a photo of a plant or flower",
}
@lru_cache(maxsize=1)
def label_vectors():
    if not semantic_available():raise RuntimeError("semantic model unavailable")
    return {name:array("f",text_embedding(prompt).tolist()) for name,prompt in LABELS.items()}

def _decode(blob):
    v=array("f");v.frombytes(blob)
    return v if len(v)==DIM else None

def _dot(a,b):return sum(x*y for x,y in zip(a,b))

def install(g:dict[str,Any])->None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"]
    def ensure(c):
        c.execute("""CREATE TABLE IF NOT EXISTS gallery_taxonomy(
          image_id INTEGER PRIMARY KEY,size INTEGER NOT NULL,mtime REAL NOT NULL,model TEXT NOT NULL,
          taxonomy_version TEXT NOT NULL,processed_at REAL NOT NULL,labels TEXT NOT NULL,
          FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE)""")
        c.execute("CREATE INDEX IF NOT EXISTS gallery_taxonomy_version_idx ON gallery_taxonomy(taxonomy_version)")
    def status(_):
        with db() as c:
            ensure(c)
            eligible=c.execute("""SELECT COUNT(*) FROM gallery_semantic s JOIN images i ON i.id=s.image_id
              WHERE s.size=i.size AND s.mtime=i.mtime AND s.model=? AND s.error IS NULL AND s.embedding IS NOT NULL""",(MODEL_ID,)).fetchone()[0]
            done=c.execute("""SELECT COUNT(*) FROM gallery_taxonomy t JOIN images i ON i.id=t.image_id
              WHERE t.size=i.size AND t.mtime=i.mtime AND t.model=? AND t.taxonomy_version=?""",(MODEL_ID,VERSION)).fetchone()[0]
        return {"ok":True,"available":semantic_available(),"eligible":eligible,"indexed":done,"remaining":max(0,eligible-done),"percent":round(done*100/eligible,2) if eligible else 100.0,"taxonomy_version":VERSION,"labels":len(LABELS),"identity_inference":False}
    def index(args):
        limit=max(1,min(int(args.get("limit",128)),1024));top_k=max(1,min(int(args.get("top_k",3)),8));vectors=label_vectors()
        with db() as c:
            ensure(c);rows=c.execute("""SELECT i.id,i.size,i.mtime,s.embedding FROM gallery_semantic s JOIN images i ON i.id=s.image_id
              LEFT JOIN gallery_taxonomy t ON t.image_id=i.id
              WHERE s.size=i.size AND s.mtime=i.mtime AND s.model=? AND s.error IS NULL AND s.embedding IS NOT NULL
              AND (t.image_id IS NULL OR t.size<>i.size OR t.mtime<>i.mtime OR t.model<>? OR t.taxonomy_version<>?)
              ORDER BY i.mtime DESC LIMIT ?""",(MODEL_ID,MODEL_ID,VERSION,limit)).fetchall()
        indexed=[];errors=[]
        for r in rows:
            try:
                emb=_decode(r["embedding"])
                if emb is None:raise ValueError("invalid embedding")
                scored=sorted(((_dot(emb,v),name) for name,v in vectors.items()),reverse=True)[:top_k]
                labels=[{"label":name,"score":round(float(score),5)} for score,name in scored]
                with db() as c:
                    ensure(c);c.execute("""INSERT INTO gallery_taxonomy(image_id,size,mtime,model,taxonomy_version,processed_at,labels)
                      VALUES(?,?,?,?,?,?,?) ON CONFLICT(image_id) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,
                      model=excluded.model,taxonomy_version=excluded.taxonomy_version,processed_at=excluded.processed_at,labels=excluded.labels""",
                      (r["id"],r["size"],r["mtime"],MODEL_ID,VERSION,time.time(),json.dumps(labels,separators=(",",":"))))
                indexed.append(int(r["id"]))
            except Exception as exc:errors.append({"id":int(r["id"]),"error":type(exc).__name__})
        return {"ok":True,"indexed_ids":indexed,"errors":errors,"taxonomy_version":VERSION,"identity_inference":False,"source_photos_modified":False}
    def image_labels(args):
        iid=int(args["id"])
        with db() as c:
            ensure(c);row=c.execute("""SELECT t.labels,t.processed_at FROM gallery_taxonomy t JOIN images i ON i.id=t.image_id
              WHERE t.image_id=? AND t.size=i.size AND t.mtime=i.mtime AND t.model=? AND t.taxonomy_version=?""",(iid,MODEL_ID,VERSION)).fetchone()
        if not row:raise ValueError("taxonomy not indexed for this image")
        return {"ok":True,"id":iid,"labels":json.loads(row["labels"]),"processed_at":row["processed_at"],"identity_inference":False}
    def search(args):
        label=str(args["label"]).strip().lower()
        if label not in LABELS:raise ValueError("unknown taxonomy label")
        min_score=float(args.get("min_score",0.0));limit=max(1,min(int(args.get("limit",100)),500));items=[]
        with db() as c:
            ensure(c);rows=c.execute("""SELECT i.*,t.labels FROM gallery_taxonomy t JOIN images i ON i.id=t.image_id
              WHERE t.size=i.size AND t.mtime=i.mtime AND t.model=? AND t.taxonomy_version=? ORDER BY i.mtime DESC""",(MODEL_ID,VERSION)).fetchall()
        for r in rows:
            labels=json.loads(r["labels"]);hit=next((x for x in labels if x["label"]==label and float(x["score"])>=min_score),None)
            if hit:
                items.append({**g["_item"](r),"taxonomy_label":label,"taxonomy_score":hit["score"]})
                if len(items)>=limit:break
        return {"ok":True,"label":label,"count":len(items),"items":items,"identity_inference":False}
    def labels(_):return {"ok":True,"labels":[{"label":k,"prompt":v} for k,v in LABELS.items()],"taxonomy_version":VERSION,"identity_inference":False}
    tools["gallery_taxonomy_status"]=(schema(),status)
    tools["gallery_taxonomy_index"]=(schema({"limit":{"type":"integer","minimum":1,"maximum":1024,"default":128},"top_k":{"type":"integer","minimum":1,"maximum":8,"default":3}}),index)
    tools["gallery_taxonomy_labels"]=(schema(),labels)
    tools["gallery_taxonomy_get"]=(schema({"id":{"type":"integer","minimum":1}},["id"]),image_labels)
    tools["gallery_taxonomy_search"]=(schema({"label":{"type":"string","enum":sorted(LABELS)},"min_score":{"type":"number","minimum":-1.0,"maximum":1.0,"default":0.0},"limit":{"type":"integer","minimum":1,"maximum":500,"default":100}},["label"]),search)
    descriptions.update({"gallery_taxonomy_status":"Read zero-shot visual taxonomy coverage over local CLIP embeddings.","gallery_taxonomy_index":"Assign generic visual labels locally from existing CLIP embeddings; no biometric identity inference.","gallery_taxonomy_labels":"List supported generic zero-shot visual labels.","gallery_taxonomy_get":"Read generic visual labels for one indexed image.","gallery_taxonomy_search":"Find images by a generic local visual taxonomy label."})
    readonly.update({"gallery_taxonomy_status","gallery_taxonomy_labels","gallery_taxonomy_get","gallery_taxonomy_search"})
