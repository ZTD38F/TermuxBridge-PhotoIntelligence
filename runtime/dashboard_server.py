"""Local dashboard HTTP server for Photo Intelligence.
Author: @dvkrum
"""
from __future__ import annotations
import base64,json,mimetypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
import gallery_bridge_tools as g
from photo_config import DASHBOARD_HOST,DASHBOARD_PORT,STATE_DIR
WEB=Path(__file__).resolve().parent.parent/"web"

def _json(handler,status,payload):
    raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
    handler.send_response(status);handler.send_header("Content-Type","application/json; charset=utf-8");handler.send_header("Content-Length",str(len(raw)));handler.send_header("Cache-Control","no-store");handler.send_header("X-Content-Type-Options","nosniff");handler.end_headers();handler.wfile.write(raw)

def _read_json(handler,max_bytes=65536):
    length=int(handler.headers.get("Content-Length","0") or 0)
    if length<0 or length>max_bytes:raise ValueError("request body too large")
    raw=handler.rfile.read(length) if length else b"{}"
    value=json.loads(raw.decode("utf-8"));
    if not isinstance(value,dict):raise ValueError("JSON object required")
    return value

def status():
    base=g.gallery_status({});ocr=g.gallery_ocr_status({});metadata=g.TOOLS["gallery_metadata_status"][1]({});visual=g.TOOLS["gallery_visual_status"][1]({});semantic=g.TOOLS["gallery_semantic_status"][1]({});duplicates=g.TOOLS["gallery_duplicate_status"][1]({});sources=g.TOOLS["gallery_source_status"][1]({});pipeline=g.TOOLS["gallery_pipeline_status"][1]({});health=g.TOOLS["gallery_health"][1]({});settings=g.TOOLS["gallery_settings_get"][1]({})
    sh=sources.get("sources",{}).get("android_data_shizuku",{}).get("indexed",0)
    return {"ok":True,"library":{"total":base.get("indexed",0),"shizuku":sh,"albums":base.get("albums",0),"last_scan":base.get("last_scan")},"sources":sources,"ocr":ocr,"metadata":metadata,"visual":visual,"semantic":semantic,"duplicates":duplicates,"pipeline":pipeline,"health":health,"settings":settings,"db_integrity":base.get("db_integrity"),"free_storage_gib":base.get("free_storage_gib")}

class Handler(BaseHTTPRequestHandler):
    server_version="PhotoIntelligence/0.2"
    def log_message(self,format,*args):pass
    def _error(self,exc):
        if isinstance(exc,(ValueError,KeyError,json.JSONDecodeError,PermissionError)):return _json(self,400,{"ok":False,"error":str(exc)[:200]})
        if isinstance(exc,(FileExistsError,FileNotFoundError)):return _json(self,409,{"ok":False,"error":str(exc)[:200]})
        return _json(self,500,{"ok":False,"error":type(exc).__name__})
    def do_GET(self):
        parsed=urlparse(self.path);q=parse_qs(parsed.query);path=parsed.path
        try:
            if path=="/api/status":return _json(self,200,status())
            if path=="/api/health":return _json(self,200,g.TOOLS["gallery_health"][1]({}))
            if path=="/api/pipeline":return _json(self,200,g.TOOLS["gallery_pipeline_status"][1]({}))
            if path=="/api/metrics":return _json(self,200,g.TOOLS["gallery_pipeline_metrics"][1]({}))
            if path=="/api/settings":return _json(self,200,g.TOOLS["gallery_settings_get"][1]({}))
            if path=="/api/gallery":
                args={"limit":min(200,int(q.get("limit",[60])[0])),"offset":max(0,int(q.get("offset",[0])[0])),"sort":q.get("sort",["newest"])[0]}
                if q.get("query",[""])[0]:args["query"]=q["query"][0]
                if q.get("album",[""])[0]:args["album"]=q["album"][0]
                return _json(self,200,g.gallery_list(args))
            if path=="/api/search":
                query=q.get("q",[""])[0].strip();return _json(self,200,g.TOOLS["gallery_smart_search"][1]({"query":query,"limit":min(200,int(q.get("limit",[100])[0]))}))
            if path=="/api/albums":return _json(self,200,g.gallery_albums({"limit":500}))
            if path=="/api/categories":return _json(self,200,g.TOOLS["gallery_categories"][1]({}))
            if path=="/api/smart-albums":return _json(self,200,g.TOOLS["gallery_smart_albums"][1]({}))
            if path=="/api/smart-album":
                return _json(self,200,g.TOOLS["gallery_smart_album_items"][1]({"album_id":q.get("album_id",[""])[0],"limit":min(200,int(q.get("limit",[100])[0])),"offset":max(0,int(q.get("offset",[0])[0]))}))
            if path=="/api/tags":return _json(self,200,g.TOOLS["gallery_tags"][1]({}))
            if path=="/api/tag":
                return _json(self,200,g.TOOLS["gallery_tag_items"][1]({"tag":q.get("tag",[""])[0],"limit":min(200,int(q.get("limit",[100])[0])),"offset":max(0,int(q.get("offset",[0])[0]))}))
            if path=="/api/cleanup":return _json(self,200,g.TOOLS["gallery_cleanup_candidates"][1]({"limit":200}))
            if path=="/api/cleanup/plan":return _json(self,200,g.TOOLS["gallery_cleanup_plan_get"][1]({"plan_id":q.get("plan_id",[""])[0]}))
            if path=="/api/quarantine":return _json(self,200,g.TOOLS["gallery_quarantine_status"][1]({}))
            if path=="/api/duplicates":return _json(self,200,{"ok":True,"status":g.TOOLS["gallery_duplicate_status"][1]({}),"exact":g.TOOLS["gallery_exact_duplicates"][1]({"limit":100}),"near":g.TOOLS["gallery_near_duplicates"][1]({"limit":100,"threshold":3})})
            if path=="/api/thumb":
                result=g.gallery_thumbnail({"id":int(q["id"][0]),"max_edge":384});block=result["__mcp_content__"][0];raw=base64.b64decode(block["data"])
                self.send_response(200);self.send_header("Content-Type",block["mimeType"]);self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","private,max-age=60");self.send_header("X-Content-Type-Options","nosniff");self.end_headers();self.wfile.write(raw);return
            target=(WEB/("index.html" if path=="/" else path.lstrip("/"))).resolve()
            if not target.is_file() or not target.is_relative_to(WEB.resolve()):self.send_error(404);return
            raw=target.read_bytes();self.send_response(200);self.send_header("Content-Type",mimetypes.guess_type(str(target))[0] or "application/octet-stream");self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","no-cache");self.send_header("X-Content-Type-Options","nosniff");self.end_headers();self.wfile.write(raw)
        except Exception as exc:self._error(exc)
    def do_POST(self):
        try:
            path=urlparse(self.path).path;body=_read_json(self)
            if path=="/api/settings":return _json(self,200,g.TOOLS["gallery_settings_update"][1]({"changes":body}))
            if path=="/api/reconcile":return _json(self,200,g.TOOLS["gallery_reconcile"][1]({"reason":str(body.get("reason","dashboard"))[:80]}))
            if path=="/api/smart-album/create":return _json(self,200,g.TOOLS["gallery_smart_album_create"][1]({"name":body.get("name",""),"criteria":body.get("criteria",{})}))
            if path=="/api/smart-album/delete":return _json(self,200,g.TOOLS["gallery_smart_album_delete"][1]({"album_id":body.get("album_id",""),"confirm_album_id":body.get("confirm_album_id","")}))
            if path=="/api/tag/add":return _json(self,200,g.TOOLS["gallery_tag_add"][1]({"ids":body.get("ids",[]),"tag":body.get("tag","")}))
            if path=="/api/tag/remove":return _json(self,200,g.TOOLS["gallery_tag_remove"][1]({"ids":body.get("ids",[]),"tag":body.get("tag","")}))
            if path=="/api/cleanup/plan":return _json(self,200,g.TOOLS["gallery_cleanup_plan"][1]({"ids":body.get("ids",[]),"undo_hours":body.get("undo_hours",72)}))
            if path in {"/api/cleanup/quarantine","/api/cleanup/restore","/api/cleanup/purge"}:
                tool={"/api/cleanup/quarantine":"gallery_cleanup_quarantine","/api/cleanup/restore":"gallery_cleanup_restore","/api/cleanup/purge":"gallery_cleanup_purge"}[path]
                return _json(self,200,g.TOOLS[tool][1]({"plan_id":body.get("plan_id",""),"approved":body.get("approved") is True,"confirm_plan_id":body.get("confirm_plan_id","")}))
            self.send_error(404)
        except Exception as exc:self._error(exc)

def main():
    server=ThreadingHTTPServer((DASHBOARD_HOST,DASHBOARD_PORT),Handler);print(f"Photo Intelligence dashboard http://{DASHBOARD_HOST}:{DASHBOARD_PORT}",flush=True);server.serve_forever()
if __name__=="__main__":main()
