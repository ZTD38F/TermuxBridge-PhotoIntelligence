"""Local dashboard HTTP server for Photo Intelligence.
Author: @dvkrum
"""
from __future__ import annotations
import base64, json, mimetypes, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import gallery_bridge_tools as g
from photo_config import DASHBOARD_HOST,DASHBOARD_PORT,STATE_DIR
WEB=Path(__file__).resolve().parent.parent/"web"

def _json(handler,status,payload):
    raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
    handler.send_response(status); handler.send_header("Content-Type","application/json; charset=utf-8"); handler.send_header("Content-Length",str(len(raw))); handler.send_header("Cache-Control","no-store"); handler.end_headers(); handler.wfile.write(raw)

def _full_status():
    p=STATE_DIR/"gallery_full_ocr_status.json"
    try:return json.loads(p.read_text())
    except Exception:return {"state":"NOT_STARTED"}

def status():
    base=g.gallery_status({}); ocr=g.gallery_ocr_status({}); visual=g.TOOLS["gallery_visual_status"][1]({}); semantic=g.TOOLS["gallery_semantic_status"][1]({}); duplicates=g.TOOLS["gallery_duplicate_status"][1]({})
    with g._db() as c:
        sh=c.execute("SELECT COUNT(*) FROM images WHERE path LIKE 'shizuku://%'").fetchone()[0]
    return {"ok":True,"library":{"total":base.get("indexed",0),"shizuku":sh},"ocr":ocr,"visual":visual,"semantic":semantic,"duplicates":duplicates,"full":_full_status(),"db_integrity":base.get("db_integrity"),"free_storage_gib":base.get("free_storage_gib")}

class Handler(BaseHTTPRequestHandler):
    server_version="PhotoIntelligence/0.1"
    def log_message(self,format,*args): pass
    def do_GET(self):
        parsed=urlparse(self.path); q=parse_qs(parsed.query); path=parsed.path
        try:
            if path=="/api/status": return _json(self,200,status())
            if path=="/api/gallery":
                args={"limit":min(200,int(q.get("limit",[60])[0])),"offset":max(0,int(q.get("offset",[0])[0])),"sort":q.get("sort",["newest"])[0]}
                if q.get("query",[""])[0]: args["query"]=q["query"][0]
                if q.get("album",[""])[0]: args["album"]=q["album"][0]
                return _json(self,200,g.gallery_list(args))
            if path=="/api/search":
                query=q.get("q",[""])[0].strip()
                return _json(self,200,g.TOOLS["gallery_smart_search"][1]({"query":query,"limit":min(200,int(q.get("limit",[100])[0]))}))
            if path=="/api/albums": return _json(self,200,g.gallery_albums({"limit":500}))
            if path=="/api/categories": return _json(self,200,g.TOOLS["gallery_categories"][1]({}))
            if path=="/api/cleanup": return _json(self,200,g.TOOLS["gallery_cleanup_candidates"][1]({"limit":200}))
            if path=="/api/duplicates": return _json(self,200,{"ok":True,"status":g.TOOLS["gallery_duplicate_status"][1]({}),"exact":g.TOOLS["gallery_exact_duplicates"][1]({"limit":100}),"near":g.TOOLS["gallery_near_duplicates"][1]({"limit":100,"threshold":3})})
            if path=="/api/thumb":
                result=g.gallery_thumbnail({"id":int(q["id"][0]),"max_edge":384}); block=result["__mcp_content__"][0]; raw=base64.b64decode(block["data"])
                self.send_response(200); self.send_header("Content-Type",block["mimeType"]); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","private,max-age=60"); self.end_headers(); self.wfile.write(raw); return
            target=WEB/("index.html" if path=="/" else path.lstrip("/"))
            target=target.resolve()
            if not target.is_file() or not target.is_relative_to(WEB.resolve()): self.send_error(404); return
            raw=target.read_bytes(); self.send_response(200); self.send_header("Content-Type",mimetypes.guess_type(str(target))[0] or "application/octet-stream"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
        except (ValueError,KeyError) as exc: _json(self,400,{"ok":False,"error":str(exc)[:200]})
        except Exception as exc: _json(self,500,{"ok":False,"error":type(exc).__name__})

def main():
    server=ThreadingHTTPServer((DASHBOARD_HOST,DASHBOARD_PORT),Handler)
    print(f"Photo Intelligence dashboard http://{DASHBOARD_HOST}:{DASHBOARD_PORT}",flush=True)
    server.serve_forever()
if __name__=="__main__": main()
