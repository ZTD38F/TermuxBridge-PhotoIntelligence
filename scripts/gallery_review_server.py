#!/usr/bin/env python3
"""Loopback-only, authenticated, durable manual-photo-review state service.

No biometric matching, no app-private Android access, no original media writes.
Author: @dvkrum.
"""
from __future__ import annotations
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from gallery_album_review import VALID_REVIEWS, render_html, _safe_local_path, _live_fingerprint


def _persisted_path(state: Path, scope: str) -> Path:
    prefix = "photo-review-v1:"
    if not scope.startswith(prefix) or len(scope) != len(prefix) + 16:
        raise ValueError("invalid review scope")
    digest = scope[len(prefix):]
    if not all(x in "0123456789abcdef" for x in digest):
        raise ValueError("invalid review scope")
    return state / "review_decisions" / (digest + ".json")


def _read_snapshot(path: Path, scope: str) -> dict:
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("unsafe review snapshot")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("schema") != 1 or data.get("kind") != "manual-photo-review"
        or data.get("scope") != scope or not isinstance(data.get("decisions"),dict)):
        raise ValueError("invalid stored review snapshot")
    return data["decisions"]


def load_decisions(state: Path, scope: str) -> dict:
    """Use a private file; don't trust unscoped localStorage."""
    return _read_snapshot(_persisted_path(state,scope),scope)


def persist_reviews(state: Path, scope: str, updates: list[dict],
                    valid_fingerprints: dict[int,tuple],
                    shared_root: Path) -> int:
    """One atomic, locked write for a bounded batch of human-reviewed marks."""
    if not isinstance(updates,list) or not 1<=len(updates)<=5000:
        raise ValueError("invalid batch length")
    root=shared_root.resolve(strict=True)
    for item in updates:
        if not isinstance(item,dict) or set(item)!={"id","decision","fingerprint"}:
            raise ValueError("invalid review fields")
        image_id,decision,fingerprint=item["id"],item["decision"],item["fingerprint"]
        if (not isinstance(image_id,int) or isinstance(image_id,bool)
            or image_id not in valid_fingerprints
            or (decision not in VALID_REVIEWS and decision!="clear")
            or not isinstance(fingerprint,str)):
            raise ValueError("invalid or stale image review")
        expected_digest,rel,expected_size,expected_mtime=valid_fingerprints[image_id]
        if expected_digest!=fingerprint:
            raise ValueError("review fingerprint mismatch")
        path=_safe_local_path(root,rel)
        if path is None or path.is_symlink():
            raise ValueError("unsafe reviewed path")
        try:
            info=path.stat()
            if (not path.is_file() or info.st_size!=expected_size
                or abs(info.st_mtime-expected_mtime)>3):
                raise ValueError("reviewed image no longer matches catalog")
        except OSError as exc:
            raise ValueError("reviewed image missing") from exc
    destination = _persisted_path(state,scope)
    destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if destination.parent.is_symlink():
        raise ValueError("review storage cannot be a symlink")
    destination.parent.chmod(0o700)
    lock_path=destination.parent/(destination.stem+".lock")
    fd=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        decisions=_read_snapshot(destination,scope)
        for item in updates:
            if item["decision"]=="clear":
                decisions.pop(str(item["id"]),None)
            else:
                decisions[str(item["id"])]={
                    "decision":item["decision"],
                    "fingerprint":item["fingerprint"],
                }
        payload=json.dumps({"schema":1,"kind":"manual-photo-review",
                            "scope":scope,"decisions":decisions,
                            "verified_identity_by_model":False},
                           ensure_ascii=False,separators=(",",":")).encode("utf-8")
        if len(payload)>5*1024*1024:
            raise ValueError("review data too large")
        tmp=destination.parent/("."+destination.stem+"."+secrets.token_hex(12)+".tmp")
        tfd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_CLOEXEC,0o600)
        try:
            with os.fdopen(tfd,"wb") as f:
                f.write(payload);f.flush();os.fsync(f.fileno())
            os.replace(tmp,destination)
            dirfd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(dirfd)
            finally:os.close(dirfd)
        finally:
            if tmp.exists():tmp.unlink()
        return len(decisions)
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def persist_review(state: Path, scope: str, image_id: int, decision: str,
                   image_fingerprint: str, valid_fingerprints: dict[int,tuple],
                   shared_root: Path) -> int:
    return persist_reviews(state,scope,[{"id":image_id,"decision":decision,
                                        "fingerprint":image_fingerprint}],
                           valid_fingerprints,shared_root)


def image_fingerprint(item: dict) -> str:
    """Single canonical browser/server binding for an unchanged media record."""
    return _live_fingerprint(item)


def validated_state_decisions(report:dict, state:Path) -> dict[int,str]:
    """Reject persisted decisions whose image IDs now refer to changed media."""
    stored=load_decisions(state,report["review_scope"])
    fingerprints={int(item["id"]):image_fingerprint(item)
                  for item in report["candidates"]}
    return {int(k):v["decision"] for k,v in stored.items()
            if k.isdecimal() and int(k) in fingerprints and isinstance(v,dict)
            and v.get("fingerprint")==fingerprints[int(k)]
            and v.get("decision") in VALID_REVIEWS}


def _server_page(report: dict, state: Path, page_size: int) -> tuple[str,dict[int,str]]:
    scope=report["review_scope"]
    approved=validated_state_decisions(report,state)
    rendered=dict(report)
    rendered["reviewed_decisions"]={
        **report.get("reviewed_decisions",{}), **approved}
    html=render_html(rendered,page=1,page_size=page_size,live_mode=True)
    return html,approved


def make_server(report: dict, state: Path, page_size: int = 96,
                port: int = 0,
                shared_root: Path=Path("/storage/emulated/0")
                ) -> tuple[ThreadingHTTPServer,str]:
    if not 1<=page_size<=200:
        raise ValueError("page size")
    state=state.resolve(strict=True)
    token=secrets.token_urlsafe(30)
    # Capture only metadata of current candidates, no pixels.
    valid={int(item["id"]):(
        image_fingerprint(item),str(item["path"]),int(item["size"]),float(item["mtime"]))
        for item in report["candidates"]}
    page,approved=_server_page(report,state,page_size)
    address_prefix="/"+token
    server_started=time.monotonic()
    last_activity={"time":server_started}
    class ReviewHandler(BaseHTTPRequestHandler):
        server_version="PrivatePhotoReview/1.0"
        sys_version=""
        def log_message(self,fmt,*args):  # never disclose token or filenames
            pass
        def _headers(self,status:int,length:int,content_type:str):
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(length))
            self.send_header("Cache-Control","no-store")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("X-Frame-Options","DENY")
            self.send_header("Cross-Origin-Resource-Policy","same-origin")
            self.end_headers()
        def _reply(self,status:int,data:bytes,type_:str="application/json; charset=utf-8"):
            self._headers(status,len(data),type_)
            self.wfile.write(data)
        def do_GET(self):
            requested=urlsplit(self.path).path
            if requested==address_prefix+"/":
                last_activity["time"]=time.monotonic()
                self._reply(200,page.encode("utf-8"),"text/html; charset=utf-8")
                return
            self._reply(404,b'{"ok":false}')
        def do_POST(self):
            path=urlsplit(self.path).path
            if path not in (address_prefix+"/api/review",
                            address_prefix+"/api/import"):
                self._reply(404,b'{"ok":false}')
                return
            # All requests originate from the loopback page and carry a
            # non-simple custom header; deny CSRF/cross-origin POST attempts.
            origin=self.headers.get("Origin","")
            host=f"http://127.0.0.1:{self.server.server_port}"
            if (origin != host or
                self.headers.get("X-Photo-Review")!="manual-user-gesture" or
                self.headers.get("Content-Type","").split(";")[0]!="application/json"):
                self._reply(403,b'{"ok":false,"error":"origin"}')
                return
            try:
                length=int(self.headers.get("Content-Length","-1"))
                max_size=1024*1024 if path.endswith("/api/import") else 4096
                if not 0<=length<=max_size:
                    raise ValueError("body length")
                self.connection.settimeout(8)
                raw=self.rfile.read(length)
                body=json.loads(raw)
                if path.endswith("/api/import"):
                    if not isinstance(body,dict) or set(body)!={"updates"}:
                        raise ValueError("invalid import")
                    total=persist_reviews(state,report["review_scope"],
                                          body["updates"],valid,shared_root)
                else:
                    if not isinstance(body,dict) or set(body)!={"id","decision","fingerprint"}:
                        raise ValueError("invalid fields")
                    total=persist_review(state,report["review_scope"],body["id"],
                                         body["decision"],body["fingerprint"],valid,shared_root)
                last_activity["time"]=time.monotonic()
                data=json.dumps({"ok":True,"saved":True,"count":total}).encode()
                self._reply(200,data)
            except (ValueError,TypeError,json.JSONDecodeError):
                self._reply(422,b'{"ok":false,"error":"invalid-review"}')
            except (OSError,sqlite3.Error):
                self._reply(500,b'{"ok":false,"error":"persistence"}')
    server=ThreadingHTTPServer(("127.0.0.1",port),ReviewHandler)
    server.daemon_threads=True
    server.last_activity=last_activity
    server.started_at=server_started
    url=f"http://127.0.0.1:{server.server_port}{address_prefix}/"
    return server,url


def serve(report: dict, state: Path, page_size: int=96, port: int=0) -> int:
    server,url=make_server(report,state,page_size,port)
    print(json.dumps({"ok":True,"url":url,"bind":"127.0.0.1",
                      "candidates":report["candidate_count"],
                      "persisted_state_dir":str(state/"review_decisions"),
                      "photos_modified":False,"identity_recognition":False}),
          flush=True)
    server.timeout=3
    try:
        # Transient on-demand session; no indefinite background service.
        while (time.monotonic()-server.last_activity["time"]<3600
               and time.monotonic()-server.started_at<21600):
            server.handle_request()
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return 0
