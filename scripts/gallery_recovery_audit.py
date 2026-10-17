#!/usr/bin/env python3
"""Read-only comparison of a historical gallery snapshot with current files.

Never deletes/changes media or index. Metadata-only matches are not byte proof.
Author: @dvkrum
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3


def catalog(path: Path) -> dict[str, tuple[int,float,str]]:
    c = sqlite3.connect(f"file:{path.resolve(strict=True)}?mode=ro",uri=True,timeout=8)
    try:
        if c.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("corrupted SQLite source")
        return {str(p):(int(s or 0),float(mt or 0),str(name))
                for p,s,mt,name in c.execute("select path,size,mtime,name from images")}
    finally:
        c.close()


def safe_path(root: Path, rel: str) -> Path | None:
    if (not rel or rel.startswith("/") or "\x00" in rel
        or any(x in ("",".","..") for x in rel.split("/"))):
        return None
    try:
        path=root/rel
        if not path.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
            return None
        return path
    except OSError:
        return None


def audit(before: Path, now: Path, root: Path, search_files: bool=True) -> dict:
    old=catalog(before)
    current=catalog(now)
    lost={p:m for p,m in old.items() if p not in current}
    protected=sum(p.startswith("shizuku://") for p in lost)
    missing={p:m for p,m in lost.items() if not p.startswith("shizuku://")}
    physically_missing={}
    still_exists=[]
    for rel,meta in missing.items():
        dest=safe_path(root,rel)
        try:
            if dest and not dest.is_symlink() and dest.is_file():
                still_exists.append(rel)
            else:physically_missing[rel]=meta
        except OSError:
            physically_missing[rel]=meta
    names=defaultdict(list)
    for p,(_,_,name) in physically_missing.items():names[name].append(p)
    matches=defaultdict(list)
    checked=set()

    def consider(rel,size,mtime,name):
        if rel in checked:return
        checked.add(rel)
        for former in names.get(name,[]):
            original_size,original_mtime,_=physically_missing[former]
            if size!=original_size:continue
            kind="probable_relocation" if abs(mtime-original_mtime)<3 else "possible_copy"
            matches[former].append({"current_path":rel,"classification":kind,
                                    "bytes_verified_equal":False})

    for rel,(size,mtime,name) in current.items():
        if name not in names:continue
        path=safe_path(root,rel)
        if path is None:continue
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_size==size:
                consider(rel,size,path.stat().st_mtime,name)
        except OSError:pass
    if search_files:
        for parent in ("DCIM","Pictures","Download","Movies","Documents","Android/media","MIUI"):
            base=safe_path(root,parent)
            if base is None or not base.is_dir():continue
            for directory,dirs,files in os.walk(base,followlinks=False):
                dirs[:]=[d for d in dirs if d!="node_modules" and not
                         (Path(directory)/d).is_symlink()]
                for name in files:
                    if name not in names:continue
                    path=Path(directory)/name
                    try:
                        if path.is_symlink():continue
                        stat=path.stat()
                        consider(str(path.relative_to(root)),stat.st_size,stat.st_mtime,name)
                    except OSError:pass
    strong={p for p, candidates in matches.items()
            if any(x["classification"]=="probable_relocation" for x in candidates)}
    copies=set(matches)-strong
    unresolved=set(physically_missing)-strong-copies
    return {
        "recorded_at":datetime.now(timezone.utc).isoformat(),
        "read_only":True,"image_bytes_read":False,"photos_modified":False,
        "baseline_rows":len(old),"current_rows":len(current),
        "missing_index_rows":len(lost),"new_index_rows":len(set(current)-set(old)),
        "protected_rows_missing":protected,
        "physically_missing_paths":len(physically_missing),
        "missing_bytes":sum(s for s,_,_ in physically_missing.values()),
        "missing_camera_paths":sum(p.startswith("DCIM/Camera/") for p in physically_missing),
        "missing_camera_bytes":sum(s for p,(s,_,_) in physically_missing.items()
                                   if p.startswith("DCIM/Camera/")),
        "probable_relocations":len(strong),"possible_copies":len(copies),
        "unresolved_paths":len(unresolved),"missing_but_present":len(still_exists),
        "missing_folders":dict(Counter(str(Path(p).parent) for p in physically_missing).most_common()),
        "matches":dict(sorted(matches.items())),
        "unresolved":sorted(unresolved),
        "note":"Identical filename+size+mtime is strong metadata evidence, not proof of identical bytes; deleted originals cannot be reconstructed from database metadata."
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    state=Path.home()/".termux-mcp-bridge/addons/photo-intelligence/state"
    backups=sorted((state/"backups").glob("pre-v067-*/gallery.sqlite3"))
    parser.add_argument("--baseline",type=Path,default=backups[-1] if backups else None)
    parser.add_argument("--current",type=Path,default=state/"gallery.sqlite3")
    parser.add_argument("--root",type=Path,default=Path("/storage/emulated/0"))
    parser.add_argument("--skip-file-search",action="store_true")
    parser.add_argument("--save-private",action="store_true")
    args=parser.parse_args()
    if args.baseline is None:parser.error("missing baseline SQLite")
    result=audit(args.baseline,args.current,args.root,not args.skip_file_search)
    if args.save_private:
        directory=state/"forensics"
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        directory.chmod(0o700)
        output=directory/("recovery-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".json")
        with os.fdopen(os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"w") as f:
            json.dump(result,f,ensure_ascii=False,indent=2)
            f.write("\n")
        result["private_report"]=str(output)
    print(json.dumps({k:v for k,v in result.items() if k not in
          ("matches","unresolved","missing_folders")},ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
