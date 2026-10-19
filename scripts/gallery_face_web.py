#!/usr/bin/env python3
"""On-demand loopback people gallery: optional user-confirmed group names."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from gallery_face_groups import render_private_gallery, name_group


def make_server(report:dict,faces_db:Path,port:int=0):
    token=secrets.token_urlsafe(30)
    prefix="/"+token
    allowed_ids={int(g["id"]) for g in report["groups"]}
    page=render_private_gallery(report,live=True).encode("utf-8")
    last={"time":time.monotonic()}
    class Handler(BaseHTTPRequestHandler):
        server_version="PrivateFaceAlbums/1.0"
        sys_version=""
        def log_message(self,format,*args):
            pass
        def send(self,status:int,blob:bytes,content_type:str):
            self.send_response(status)
            for key,value in (
                ("Content-Type",content_type),
                ("Content-Length",str(len(blob))),
                ("Cache-Control","no-store"),
                ("Referrer-Policy","no-referrer"),
                ("X-Content-Type-Options","nosniff"),
                ("X-Frame-Options","DENY"),
            ):
                self.send_header(key,value)
            self.end_headers()
            self.wfile.write(blob)
        def do_GET(self):
            if urlsplit(self.path).path==prefix+"/":
                last["time"]=time.monotonic()
                self.send(200,page,"text/html; charset=utf-8")
            else:
                self.send(404,b'{"ok":false}',"application/json")
        def do_POST(self):
            if urlsplit(self.path).path!=prefix+"/api/name":
                self.send(404,b'{"ok":false}',"application/json")
                return
            origin="http://127.0.0.1:"+str(self.server.server_port)
            if (self.headers.get("Origin")!=origin
                or self.headers.get("X-Face-Groups")!="manual-user-gesture"
                or self.headers.get("Content-Type","").split(";")[0]!="application/json"):
                self.send(403,b'{"ok":false}',"application/json")
                return
            try:
                length=int(self.headers.get("Content-Length","-1"))
                if not 0<=length<=2048:
                    raise ValueError("request too large")
                self.connection.settimeout(6)
                obj=json.loads(self.rfile.read(length))
                if not isinstance(obj,dict) or set(obj)!={"group_id","name"}:
                    raise ValueError("incorrect form")
                gid=obj["group_id"]
                if not isinstance(gid,int) or isinstance(gid,bool) or gid not in allowed_ids:
                    raise ValueError("unknown group")
                result=name_group(faces_db,gid,obj["name"])
                last["time"]=time.monotonic()
                body=json.dumps(result,ensure_ascii=False).encode("utf-8")
                self.send(200,body,"application/json; charset=utf-8")
            except (ValueError,TypeError):
                self.send(422,b'{"ok":false,"error":"invalid"}',"application/json")
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    server.daemon_threads=True
    server.last_activity=last
    server.started_at=time.monotonic()
    url=f"http://127.0.0.1:{server.server_port}{prefix}/"
    return server,url


def serve(report:dict,faces_db:Path,port:int=0) -> int:
    server,url=make_server(report,faces_db,port)
    print(json.dumps({
        "ok":True,"url":url,"bind":"127.0.0.1",
        "groups":len(report["groups"]),
        "auto_cluster":True,"auto_recognized_names":False,
        "no_photo_writes":True},ensure_ascii=False),flush=True)
    server.timeout=3
    try:
        while (time.monotonic()-server.last_activity["time"]<3600
               and time.monotonic()-server.started_at<21600):
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
