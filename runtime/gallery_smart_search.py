"""Hybrid local retrieval across metadata, OCR and CLIP semantics.
Author: @dvkrum
"""
from __future__ import annotations
import base64,io,json,math,re,time
from collections import defaultdict
from typing import Any
from gallery_ocr import _fts_terms

# Small visual-domain lexicon improves CLIP text prompts without sending text to a
# translation service. The original query is always retained for OCR/metadata.
VISUAL_LEXICON={
 "черный":"black","чёрный":"black","черная":"black","чёрная":"black","черное":"black","чёрное":"black","черные":"black","чёрные":"black",
 "белый":"white","белая":"white","белое":"white","белые":"white","красный":"red","красная":"red","красное":"red","красные":"red",
 "синий":"blue","синяя":"blue","синее":"blue","синие":"blue","зелёный":"green","зеленый":"green","зелёная":"green","зеленая":"green","зелёное":"green","зеленое":"green",
 "рубашка":"shirt","футболка":"t-shirt","коробка":"box","кухня":"kitchen","чек":"receipt","документ":"document","машина":"car",
 "собака":"dog","кошка":"cat","еда":"food","телефон":"phone","экран":"screen","билет":"ticket","море":"sea","пляж":"beach","дом":"home",
 "комната":"room","стол":"table","ноутбук":"laptop","наушники":"headphones","бутылка":"bottle","человек":"person","цветы":"flowers",
 "melns":"black","melna":"black","balts":"white","balta":"white","sarkans":"red","sarkana":"red","zils":"blue","zila":"blue","zaļš":"green",
 "krekls":"shirt","kaste":"box","virtuve":"kitchen","čeks":"receipt","dokuments":"document","mašīna":"car","suns":"dog","kaķis":"cat",
 "ēdiens":"food","telefons":"phone","ekrāns":"screen","biļete":"ticket","jūra":"sea","pludmale":"beach","māja":"home","istaba":"room",
 "galds":"table","dators":"computer","austiņas":"headphones","pudele":"bottle","cilvēks":"person","ziedi":"flowers"
}
TOKEN_RE=re.compile(r"[\w\-āčēģīķļņšūžА-Яа-яЁё]+",re.UNICODE)

def semantic_variants(query:str)->list[str]:
    tokens=TOKEN_RE.findall(query.casefold());mapped=[VISUAL_LEXICON.get(t,t) for t in tokens]
    translated=" ".join(mapped).strip();out=[query.strip()]
    if translated and translated.casefold()!=query.strip().casefold():out.append(translated)
    return out[:2]

def _age_boost(mtime:float)->float:
    age=max(0.0,time.time()-float(mtime or 0))/86400.0
    return 0.012/(1.0+age/365.0)

def _asset_penalty(row)->tuple[float,str|None]:
    name=str(row["name"] or "").casefold();path=str(row["path"] or "").casefold()
    if "sticker" in name or "/stickers/" in path:return -0.028,"sticker"
    if "/cache/" in path or "/.cache/" in path:return -0.018,"cache"
    if "thumbnail" in name or "/thumbnails/" in path:return -0.012,"thumbnail"
    return 0.0,None

def install(g:dict[str,Any])->None:
    schema=g["_schema"];tools=g["TOOLS"];descriptions=g["DESCRIPTIONS"];readonly=g["READ_ONLY"];db=g["_db"];item=g["_item"]
    semantic=tools["gallery_semantic_search"][1]

    def hybrid(args):
        query=str(args.get("query","")).strip()
        if not query:raise ValueError("query is required")
        limit=max(1,min(int(args.get("limit",60)),200));candidate_limit=max(60,min(600,max(limit*5,int(args.get("candidate_limit",limit*5)))))
        album=str(args.get("album","")).strip();source=str(args.get("source","")).strip();sort=str(args.get("sort","relevance"));date_from=g["_parse_date"](args.get("date_from"));date_to=g["_parse_date"](args.get("date_to"))
        if source not in {"","local","android_data"}:raise ValueError("invalid source")
        if sort not in {"relevance","newest"}:raise ValueError("invalid sort")
        scores=defaultdict(float);signals=defaultdict(list);rows_by_id={};semantic_scores={};semantic_contrib={}
        qcf=query.casefold();needle="%"+query+"%"
        filters=[];fparams=[]
        if album:filters.append("album=?");fparams.append(album)
        if source=="local":filters.append("path NOT LIKE 'shizuku://%'")
        elif source=="android_data":filters.append("path LIKE 'shizuku://%'")
        if date_from is not None:filters.append("mtime>=?");fparams.append(date_from)
        if date_to is not None:filters.append("mtime<=?");fparams.append(date_to)
        extra=(" AND "+" AND ".join(filters)) if filters else ""
        # Metadata candidates. Rank plus field-specific exactness bonus.
        with db() as c:
            mrows=c.execute(f"SELECT * FROM images WHERE (name LIKE ? OR album LIKE ? OR path LIKE ?){extra} ORDER BY mtime DESC LIMIT ?",[needle,needle,needle,*fparams,candidate_limit]).fetchall()
        for rank,row in enumerate(mrows,1):
            iid=int(row["id"]);rows_by_id[iid]=row;name=str(row["name"]).casefold();alb=str(row["album"]).casefold();path=str(row["path"]).casefold();bonus=.0
            if name==qcf:bonus=.08
            elif qcf in name:bonus=.05
            elif qcf in alb:bonus=.025
            elif qcf in path:bonus=.015
            val=1.15/(55+rank)+bonus;scores[iid]+=val;signals[iid].append({"signal":"metadata","rank":rank,"contribution":round(val,6)})
        # OCR FTS candidates use BM25 order; text itself is not returned here.
        try:match=_fts_terms(query)
        except ValueError:match=""
        if match:
            ow=["gallery_ocr_fts MATCH ?","i.size=o.size","i.mtime=o.mtime"];op=[match]
            if album:ow.append("i.album=?");op.append(album)
            if source=="local":ow.append("i.path NOT LIKE 'shizuku://%'")
            elif source=="android_data":ow.append("i.path LIKE 'shizuku://%'")
            if date_from is not None:ow.append("i.mtime>=?");op.append(date_from)
            if date_to is not None:ow.append("i.mtime<=?");op.append(date_to)
            with db() as c:
                try:orows=c.execute(f"""SELECT i.*,bm25(gallery_ocr_fts) AS bm FROM gallery_ocr_fts
                    JOIN gallery_ocr_index o ON o.image_id=gallery_ocr_fts.rowid JOIN images i ON i.id=o.image_id
                    WHERE {' AND '.join(ow)} ORDER BY bm25(gallery_ocr_fts),i.mtime DESC LIMIT ?""",[*op,candidate_limit]).fetchall()
                except Exception:orows=[]
            for rank,row in enumerate(orows,1):
                iid=int(row["id"]);rows_by_id[iid]=row;val=1.35/(55+rank);scores[iid]+=val;signals[iid].append({"signal":"ocr","rank":rank,"contribution":round(val,6)})
        # Semantic candidates from original + local translated visual variant.
        variants=semantic_variants(query);sem_available=False
        for variant_index,variant in enumerate(variants):
            result=semantic({"query":variant,"limit":min(200,candidate_limit)})
            if not result.get("ok"):continue
            sem_available=True
            for rank,entry in enumerate(result.get("items",[]),1):
                if album and entry.get("album")!=album:continue
                path=str(entry.get("path",""));is_remote=path.startswith("shizuku://")
                if source=="local" and is_remote:continue
                if source=="android_data" and not is_remote:continue
                mt=float(entry.get("mtime",0) or 0)
                if date_from is not None and mt<date_from:continue
                if date_to is not None and mt>date_to:continue
                iid=int(entry["id"]);sem_score=float(entry.get("semantic_score",0));semantic_scores[iid]=max(semantic_scores.get(iid,-10),sem_score)
                weight=1.0 if variant_index==0 else .92;val=(0.65+max(0.0,sem_score))*weight/(55+rank)
                semantic_contrib[iid]=max(semantic_contrib.get(iid,0.0),val)
                signals[iid].append({"signal":"semantic","rank":rank,"variant":variant,"similarity":round(sem_score,5),"contribution_candidate":round(val,6)})
                if iid not in rows_by_id:
                    with db() as c:row=c.execute("SELECT * FROM images WHERE id=?",(iid,)).fetchone()
                    if row is not None:rows_by_id[iid]=row
        # Add only the strongest semantic variant per image, then gentle recency tie-breaker.
        for iid,val in semantic_contrib.items():
            scores[iid]+=val
            for sig in signals[iid]:
                if sig.get("signal")=="semantic":sig["selected_contribution"]=round(val,6) if abs(sig.get("contribution_candidate",0)-val)<1e-9 else 0.0
        for iid,row in rows_by_id.items():
            scores[iid]+=_age_boost(float(row["mtime"] or 0))
            penalty,reason=_asset_penalty(row)
            if penalty and not any(x.get("signal") in {"metadata","ocr"} for x in signals[iid]):
                scores[iid]+=penalty;signals[iid].append({"signal":"asset_penalty","reason":reason,"contribution":penalty})
        ranked=list(scores)
        if sort=="newest":ranked.sort(key=lambda iid:(float(rows_by_id[iid]["mtime"] or 0),scores[iid]),reverse=True)
        else:ranked.sort(key=lambda iid:(scores[iid],float(rows_by_id[iid]["mtime"] or 0)),reverse=True)
        ranked=ranked[:limit];max_score=max((scores[x] for x in ranked),default=1.0) or 1.0;items=[]
        for iid in ranked:
            row=rows_by_id[iid];base=item(row);sigs=signals[iid];matches=[]
            for sig in sigs:
                if sig["signal"] not in matches:matches.append(sig["signal"])
            base.update({"match":matches,"retrieval_score":round(scores[iid]/max_score,6),"retrieval_raw":round(scores[iid],6),"reasons":sigs})
            if iid in semantic_scores:base["semantic_score"]=round(semantic_scores[iid],5)
            items.append(base)
        return {"ok":True,"query":query,"query_variants":variants,"count":len(items),"items":items,"semantic_available":sem_available,"search_mode":"hybrid-rrf-v2","candidate_pool":len(scores)}

    def smart(args):
        return hybrid({"query":args.get("query",""),"limit":args.get("limit",60)})

    def search_sheet(args):
        count=max(1,min(int(args.get("limit",24)),36));cols=max(1,min(int(args.get("columns",6)),6))
        result=hybrid({"query":args.get("query",""),"limit":count,"candidate_limit":max(80,count*5),
                       **({"album":args["album"]} if args.get("album") else {}),
                       **({"source":args["source"]} if args.get("source") else {})})
        entries=result.get("items",[])
        if not entries:return {"ok":True,"count":0,"message":"No matching images","query":result.get("query")}
        cell_w,image_h,label_h=240,180,36;cols=min(cols,len(entries));rows_n=math.ceil(len(entries)/cols)
        sheet=g["Image"].new("RGB",(cols*cell_w,rows_n*(image_h+label_h)),"white");draw=g["ImageDraw"].Draw(sheet);font=g["ImageFont"].load_default(size=16)
        mapped=[]
        with db() as c:row_map={int(r["id"]):r for r in c.execute("SELECT * FROM images WHERE id IN (%s)" % ",".join("?"*len(entries)),[int(x["id"]) for x in entries]).fetchall()}
        for idx,entry in enumerate(entries):
            iid=int(entry["id"]);row=row_map.get(iid);x0=(idx%cols)*cell_w;y0=(idx//cols)*(image_h+label_h)
            if row is not None:
                try:
                    with g["_materialize_row"](row) as path:
                        with g["Image"].open(path) as src:
                            src.seek(0);thumb=g["ImageOps"].exif_transpose(src).convert("RGB");thumb.thumbnail((cell_w,image_h),g["Image"].Resampling.LANCZOS);sheet.paste(thumb,(x0+(cell_w-thumb.width)//2,y0+(image_h-thumb.height)//2))
                except Exception:
                    draw.rectangle((x0,y0,x0+cell_w-1,y0+image_h-1),outline="black");draw.text((x0+8,y0+8),"unreadable",fill="black",font=font)
            label=f"#{iid}  {float(entry.get('retrieval_score',0)):.3f}";draw.rectangle((x0,y0+image_h,x0+cell_w-1,y0+image_h+label_h-1),fill="white");draw.text((x0+6,y0+image_h+7),label,fill="black",font=font)
            mapped.append({"id":iid,"name":entry.get("name"),"album":entry.get("album"),"score":entry.get("retrieval_score"),"match":entry.get("match"),"reasons":entry.get("reasons")})
        out=io.BytesIO();sheet.save(out,format="JPEG",quality=82,optimize=True)
        meta={"query":result.get("query"),"query_variants":result.get("query_variants"),"count":len(mapped),"items":mapped,"search_mode":result.get("search_mode")}
        return {"__mcp_content__":[{"type":"image","data":base64.b64encode(out.getvalue()).decode("ascii"),"mimeType":"image/jpeg"},{"type":"text","text":json.dumps(meta,ensure_ascii=False)}]}

    tools["gallery_smart_search"]=(schema({"query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":200,"default":60}},["query"]),smart)
    tools["gallery_hybrid_search"]=(schema({
        "query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":200,"default":60},
        "candidate_limit":{"type":"integer","minimum":60,"maximum":600},"album":{"type":"string"},"source":{"type":"string","enum":["local","android_data"]},
        "date_from":{"type":"string"},"date_to":{"type":"string"},"sort":{"type":"string","enum":["relevance","newest"],"default":"relevance"}
    },["query"]),hybrid)
    tools["gallery_search_contact_sheet"]=(schema({"query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":36,"default":24},"columns":{"type":"integer","minimum":1,"maximum":6,"default":6},"album":{"type":"string"},"source":{"type":"string","enum":["local","android_data"]}},["query"]),search_sheet)
    descriptions["gallery_smart_search"]="Hybrid local retrieval across metadata, OCR and CLIP semantic embeddings with explained ranking."
    descriptions["gallery_hybrid_search"]="Search photos with weighted metadata/OCR/CLIP fusion, RU/LV visual query expansion, filters and per-result ranking reasons."
    descriptions["gallery_search_contact_sheet"]="Run hybrid search and return a labeled contact sheet of top candidates for fast ChatGPT visual reranking."
    readonly.update({"gallery_smart_search","gallery_hybrid_search","gallery_search_contact_sheet"})
