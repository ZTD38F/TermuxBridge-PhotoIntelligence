#!/usr/bin/env python3
"""Offline, private review of generic person-photo candidates.

No biometric face comparison, identity recognition, original-media changes,
or assumptions about Google Photos / Xiaomi virtual album membership.
Author: @dvkrum
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import sqlite3
import stat


NOISE = ("stickers", ".thumbnails", "gif_preview_cache")
VALID_REVIEWS = frozenset(("mine", "other", "in_album"))
MAX_HASH_BYTES = 512 * 1024 * 1024
MAX_SINGLE_FILE = 128 * 1024 * 1024


def review_scope(album: str, manifest_fingerprint: str) -> str:
    digest = hashlib.sha256(
        ((album or "[no-album]") + "|" + manifest_fingerprint).encode("utf-8")
    ).hexdigest()[:16]
    return "photo-review-v1:" + digest


def read_reviews(path: Path, scope: str) -> dict[int,str]:
    """Only manually submitted decisions; reject cross-album/stale exports."""
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("review file too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data,dict) or data.get("schema")!=1
        or data.get("kind")!="manual-photo-review"
        or data.get("scope")!=scope
        or not isinstance(data.get("decisions"),dict)
        or len(data["decisions"])>50000):
        raise ValueError("review format or album scope mismatch; re-export latest review")
    result={}
    for key, val in data["decisions"].items():
        if (not isinstance(key,str) or not key.isdecimal()
            or int(key)<1 or int(key)>2**63-1
            or not isinstance(val,dict)
            or val.get("decision") not in VALID_REVIEWS):
            raise ValueError("invalid review decision")
        result[int(key)]=val["decision"]
    return result


def find_exported_reviews(download_dir: Path, scope: str
                          ) -> tuple[Path | None, dict[int,str]]:
    """Find newest valid manual export, only from immediate Download children."""
    if not download_dir.is_dir():
        return None, {}
    files=[]
    try:
        with os.scandir(download_dir) as entries:
            for entry in entries:
                if (not entry.name.startswith("photo-review-selections")
                    or not entry.name.endswith(".json")
                    or entry.is_symlink() or not entry.is_file(follow_symlinks=False)):
                    continue
                try:
                    size=entry.stat(follow_symlinks=False).st_size
                    if 0 < size <= 5 * 1024 * 1024:
                        files.append((entry.stat(follow_symlinks=False).st_mtime_ns,
                                      Path(entry.path)))
                except OSError:
                    continue
    except OSError:
        return None, {}
    for _,path in sorted(files,reverse=True)[:50]:
        try:
            reviewed=read_reviews(path,scope)
            return path,reviewed
        except (OSError,ValueError,TypeError):
            continue
    return None, {}


def _safe_local_path(root: Path, relative: str) -> Path | None:
    if (not isinstance(relative,str) or not relative
        or relative.startswith("/") or "\x00" in relative
        or any(x in ("",".","..") for x in relative.split("/"))):
        return None
    try:
        candidate=root/relative
        if not candidate.resolve(strict=False).is_relative_to(root):
            return None
        return candidate
    except OSError:
        return None


def _sha256_file(root: Path, rel: str, size: int, mtime: float) -> str | None:
    """Exact byte digest only, guarded path, max size, read-only descriptor."""
    if size<=0 or size>MAX_SINGLE_FILE:
        return None
    path=_safe_local_path(root,rel)
    if path is None:
        return None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        try:
            initial=os.fstat(fd)
            if (not stat.S_ISREG(initial.st_mode) or initial.st_size!=size
                or abs(initial.st_mtime-mtime)>3):
                return None
            digest=hashlib.sha256()
            remaining=size
            while remaining:
                chunk=os.read(fd,min(1024*1024,remaining))
                if not chunk:return None
                digest.update(chunk);remaining-=len(chunk)
            final=os.fstat(fd)
            if (final.st_size!=initial.st_size
                or final.st_mtime_ns!=initial.st_mtime_ns):
                return None
            return digest.hexdigest()
        finally:
            os.close(fd)
    except OSError:
        return None


def annotate_exact_copies(report:dict, db_path:Path, decisions:dict[int,str],
                          root:Path=Path("/storage/emulated/0")) -> dict:
    """Find EXACT copies of previously reviewed images, not similar faces."""
    output=dict(report)
    output["reviewed_decisions"]=dict(decisions)
    output["exact_file_copies"]={}
    output["feedback_stats"]={"manual_decisions":len(decisions),
                              "usable_references":0,"byte_verified_copies":0,
                              "hash_bytes_read":0,"hash_budget_exhausted":False,
                              "identity_model_trained":False}
    if not decisions:return output
    root=root.resolve(strict=True)
    c=sqlite3.connect(f"file:{db_path.resolve(strict=True)}?mode=ro",
                      uri=True,timeout=8)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        refs={}
        for i in decisions:
            r=c.execute("SELECT id,path,size,mtime FROM images WHERE id=?"
                        " AND path NOT LIKE 'shizuku://%'",(i,)).fetchone()
            if r:refs[i]=r
        needed={r["size"] for r in refs.values()
                if 0<int(r["size"])<=MAX_SINGLE_FILE}
        if not needed:return output
        refs_by_digest={}
        spent=0
        for i,r in refs.items():
            size=int(r["size"])
            if size not in needed or spent+size>MAX_HASH_BYTES:
                output["feedback_stats"]["hash_budget_exhausted"]=True
                continue
            h=_sha256_file(root,r["path"],size,float(r["mtime"]))
            spent+=size
            if h:
                refs_by_digest.setdefault((size,h),[]).append(
                    (i,decisions[i]))
        output["feedback_stats"]["usable_references"]=sum(
            len(x) for x in refs_by_digest.values())
        if not refs_by_digest:
            output["feedback_stats"]["hash_bytes_read"]=spent
            return output
        ids={x["id"] for x in report["candidates"]}
        # All candidates still require exact byte equality; size alone
        # NEVER transfers any label.
        for r in c.execute("SELECT id,path,size,mtime FROM images"
                           " WHERE path NOT LIKE 'shizuku://%'"):
            if r["id"] not in ids or r["id"] in decisions:
                continue
            size=int(r["size"])
            if size not in needed:
                continue
            if spent+size>MAX_HASH_BYTES:
                output["feedback_stats"]["hash_budget_exhausted"]=True
                break
            h=_sha256_file(root,r["path"],size,float(r["mtime"]))
            spent+=size
            matches=[(i,decision) for i,decision in
                     refs_by_digest.get((size,h),[]) if i!=r["id"]]
            if not matches:continue
            output["exact_file_copies"][int(r["id"])]={
                "same_bytes_as_reviewed_photo_ids":[i for i,_ in matches],
                "manual_review_decisions":list(sorted({x for _,x in matches})),
                "same_image_bytes":True,
                "person_identity_inferred":False,
            }
        output["feedback_stats"]["hash_bytes_read"]=spent
        output["feedback_stats"]["byte_verified_copies"]=len(
            output["exact_file_copies"])
        return output
    finally:
        c.close()


def load_manifest(path: Path | None) -> set[str]:
    """Exact relative gallery paths only. Names alone are never matched."""
    if path is None:
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("paths"), list):
        raise ValueError('manifest must be an object with "paths" array')
    if len(data["paths"]) > 50000:
        raise ValueError("too many paths")
    result = set()
    for item in data["paths"]:
        if not isinstance(item, str) or len(item) > 4096:
            raise ValueError("manifest path is invalid")
        if (not item or item.startswith("/") or "\x00" in item
                or any(p in ("", ".", "..") for p in item.split("/"))):
            raise ValueError("manifest must use safe exact relative paths")
        result.add(item)
    return result


def candidates(db_path: Path, *, album: str = "", manifest: set[str] | None = None,
               min_score: float = 0.19, include_noise: bool = False,
               require_existing: bool = True,
               shared_root: Path = Path("/storage/emulated/0")) -> dict:
    """Read cached generic CLIP labels; never infer whose face is present."""
    if not 0 <= min_score <= 1:
        raise ValueError("min_score must be in [0,1]")
    if album and (len(album) > 4096 or "\x00" in album):
        raise ValueError("invalid album")
    root = shared_root.resolve(strict=True)
    db_path = db_path.resolve(strict=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("gallery database integrity check failed")
        present_albums = {
            a: n for a, n in conn.execute(
                "SELECT album, COUNT(*) FROM images WHERE path NOT LIKE 'shizuku://%' GROUP BY album"
            )}
        rows = conn.execute("""
            SELECT i.id, i.path, i.name, i.album, i.size, i.mtime,
                   t.labels
            FROM images AS i
            JOIN gallery_taxonomy AS t ON i.id = t.image_id
              AND t.size = i.size AND t.mtime = i.mtime
            WHERE i.path NOT LIKE 'shizuku://%'
            ORDER BY i.mtime DESC, i.id DESC
        """).fetchall()
        total_indexed = conn.execute("SELECT count(*) FROM images").fetchone()[0]
        local_indexed = conn.execute(
            "SELECT count(*) FROM images WHERE path NOT LIKE 'shizuku://%'"
        ).fetchone()[0]
    finally:
        conn.close()

    confirmed_paths = manifest or set()
    results = []
    counts = {
        "matched_already_in_album": 0, "excluded_by_manifest": 0,
        "noise_filtered": 0, "files_unavailable": 0,
        "generic_person_hits": 0,
    }
    for row in rows:
        try:
            labels = json.loads(row["labels"])
        except (TypeError, ValueError):
            continue
        if not isinstance(labels, list):
            continue
        score = max((float(v.get("score", -1)) for v in labels
                     if isinstance(v, dict) and v.get("label") in ("person", "people")),
                    default=-1)
        if score < min_score:
            continue
        counts["generic_person_hits"] += 1
        if album and row["album"] == album:
            counts["matched_already_in_album"] += 1
            continue
        if row["path"] in confirmed_paths:
            counts["excluded_by_manifest"] += 1
            continue
        if not include_noise and any(x in row["path"].lower() for x in NOISE):
            counts["noise_filtered"] += 1
            continue
        if require_existing:
            location = root / row["path"]
            try:
                if location.is_symlink() or not location.is_file():
                    counts["files_unavailable"] += 1
                    continue
                st = location.stat()
                if st.st_size != row["size"] or abs(st.st_mtime - row["mtime"]) > 3:
                    counts["files_unavailable"] += 1
                    continue
            except OSError:
                counts["files_unavailable"] += 1
                continue
        results.append({
            "id": row["id"], "path": row["path"], "album": row["album"],
            "name": row["name"], "mtime": row["mtime"],
            "size": row["size"],
            "person_photo_score": round(score, 5),
        })
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "locally accessible image files with existing generic person/people labels",
        "identity_recognition": False,
        "not_verified_same_person": True,
        "reference_album": album or None,
        "reference_album_visible": album in present_albums if album else False,
        "reference_album_indexed_count": present_albums.get(album) if album else None,
        "album_membership_known": bool(confirmed_paths or (album and album in present_albums)),
        "manifest_exact_paths": len(confirmed_paths),
        "manifest_fingerprint": hashlib.sha256(
            "\n".join(sorted(confirmed_paths)).encode("utf-8")
        ).hexdigest()[:16],
        "review_scope": review_scope(
            album,hashlib.sha256(
                "\n".join(sorted(confirmed_paths)).encode("utf-8")
            ).hexdigest()[:16]),
        "all_indexed": total_indexed,
        "local_indexed": local_indexed,
        "taxonomy_indexed_local": len(rows),
        "taxonomy_coverage_incomplete": len(rows) < local_indexed,
        "min_score": min_score,
        "noise_omitted": not include_noise,
        "counts": counts,
        "candidate_count": len(results),
        "candidates": results,
    }


def _live_fingerprint(item: dict) -> str:
    # Keep stable with the strictly validated server image fingerprint.
    return hashlib.sha256(
        (str(item["path"])+"\\0"+str(item["mtime"])+"\\0"+str(item["size"])).encode("utf-8")
    ).hexdigest()[:24]


def render_html(report: dict, *, page: int, page_size: int,
                live_mode: bool=False) -> str:
    if page < 1 or not 1 <= page_size <= 200:
        raise ValueError("invalid page or page_size")
    start = (page-1)*page_size
    subset = report["candidates"][start:start+page_size]
    cards = []
    for item in subset:
        ident = int(item["id"])
        cards.append(
            f'<article class="tile" data-id="{ident}">'
            f'<img loading="lazy" alt="Фото №{ident}" '
            f'src="http://127.0.0.1:18780/api/thumb?id={ident}" />'
            f'<div class="caption"><b>№ {ident}</b> · {escape(item["album"])}'
            f'<p>{escape(item["name"])} · метка {float(item["person_photo_score"]):.2f}</p></div>'
            f'<div class="buttons">'
            f'<button onclick="mark({ident},&#39;mine&#39;)">✅ Это я</button>'
            f'<button onclick="mark({ident},&#39;other&#39;)">Не я</button>'
            f'<button onclick="mark({ident},&#39;in_album&#39;)">В альбоме</button>'
            f'</div></article>'
        )
    details = (
        f"Кандидатов: {report['candidate_count']}. "
        f"Всего местных фото: {report['local_indexed']}. "
        f"Референсный альбом: {report['reference_album'] or 'НЕ УКАЗАН'}. "
        f"Страница {page}, показано {len(subset)}."
    )
    if report["reference_album"] and not report["album_membership_known"]:
        details += " ⚠ Этот альбом не найден: ни один его снимок не исключён."
    flags = (
        "Это общие метки «человек/люди», НЕ идентификация твоего лица. "
        "Система не может подтвердить членство в альбоме Google Фото или Xiaomi. "
        "Если в альбоме есть только облачные снимки, потребуется список его файлов. "
        "Разметка остаётся в браузере до экспорта; оригиналы не меняются."
    )
    # Embed only metadata, never original pixels. Dynamic pages allow
    # reviewing the complete candidate set in a single offline document.
    dataset = json.dumps([
        {"id":int(x["id"]), "album":x["album"], "name":x["name"],
         "score":float(x["person_photo_score"]),
         "fingerprint":_live_fingerprint(x) if live_mode else None,
         "duplicate_review":report.get("exact_file_copies",{}).get(int(x["id"]))}
        for x in report["candidates"]
    ],ensure_ascii=True,separators=(",",":"))
    dataset = (dataset.replace("&",r"\u0026").replace("<",r"\u003c")
              .replace(">",r"\u003e"))
    scope = report["review_scope"].split(":")[-1]
    script = """<script>
const ALL = __REVIEW_DATA__;
const PAGE_SIZE = __REVIEW_PAGE_SIZE__;
let currentPage = __REVIEW_PAGE_NUMBER__;
const KEY = 'photo-review-v1:__REVIEW_SCOPE__';
const PRIOR_REVIEWS = __PRIOR_REVIEWS__;
const LIVE_MODE = __LIVE_MODE__;
let choices = {};
if (!LIVE_MODE) {
  try { choices = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (_) {}
}
for (const [id,decision] of Object.entries(PRIOR_REVIEWS)) {
  if (!choices[id]) choices[id]={decision,reviewed_at:'imported from export'};
}
let FILTER_MODE='all';
let FILTER_TEXT='';
function shownItems() {
  const text=FILTER_TEXT.trim().toLocaleLowerCase();
  return ALL.filter(item => {
    const state=choices[String(item.id)]?.decision || '';
    const pass=FILTER_MODE==='all' ||
      (FILTER_MODE==='unreviewed' && !state) ||
      (FILTER_MODE==='duplicates' && item.duplicate_review?.same_image_bytes) ||
      state===FILTER_MODE;
    return pass && (!text || item.name.toLocaleLowerCase().includes(text) ||
                   item.album.toLocaleLowerCase().includes(text));
  });
}
function applyFilters() {
  FILTER_MODE=document.getElementById('filterMode').value;
  FILTER_TEXT=document.getElementById('searchText').value;
  currentPage=1;
  populatePage();
}
function redraw() {
  document.querySelectorAll('article[data-id]').forEach(card => {
    const state = choices[card.dataset.id]?.decision || '';
    card.dataset.decision = state;
  });
  document.getElementById('marked').textContent = Object.keys(choices).length;
}
async function mark(id, decision) {
  const item = ALL.find(x => x.id === id);
  if (!item) return;
  if (LIVE_MODE && item.fingerprint) {
    const save = document.getElementById('saveState');
    save.textContent = 'Сохраняю…';
    try {
      const prefix = location.pathname.endsWith('/') ?
        location.pathname.slice(0,-1) : location.pathname;
      const resp = await fetch(prefix+'/api/review', {
        method: 'POST',
        mode: 'same-origin',
        credentials: 'omit',
        headers: {'Content-Type':'application/json',
                  'X-Photo-Review':'manual-user-gesture'},
        body: JSON.stringify({id,decision,fingerprint:item.fingerprint})
      });
      const answer=await resp.json();
      if (!resp.ok || !answer.saved) throw Error('save failed');
      if (decision==='clear') delete choices[id];
      else choices[id] = {decision,reviewed_at:new Date().toISOString()};
      save.textContent = '✓ Сохранено на телефоне ('+answer.count+')';
      redraw();
      if(FILTER_MODE!=='all')populatePage();
      return;
    } catch (_) {
      save.textContent = '⚠ Не сохранено на телефоне. Экспортируй отметки.';
      return;
    }
  }
  if(decision==='clear') delete choices[id];
  else choices[id] = {decision,reviewed_at:new Date().toISOString()};
  try { localStorage.setItem(KEY, JSON.stringify(choices)); } catch (_) {}
  redraw();
  if(FILTER_MODE!=='all')populatePage();
}
function exportReviews() {
  const payload = JSON.stringify({schema:1,kind:'manual-photo-review',
      scope:KEY,decisions:choices, verified_identity_by_model:false},null,2);
  const a = document.createElement('a');
  const url = URL.createObjectURL(new Blob([payload],{type:'application/json'}));
  a.href=url; a.download='photo-review-selections.json';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),2000);
}
function importReviews(input) {
  if (!input.files?.[0])return;
  const r = new FileReader();r.onload=async()=>{
    try {const x=JSON.parse(r.result);
      if(x.schema!==1||x.scope!==KEY||
          !x.decisions||typeof x.decisions!=='object')throw Error('scope/format');
      const allowed=['mine','other','in_album'];
      const newChoices={};
      const updates=[];
      const known=new Map(ALL.map(item=>[String(item.id),item]));
      for(const [id,v] of Object.entries(x.decisions)) {
        if(!/^[0-9]+$/.test(id)||!allowed.includes(v?.decision))continue;
        const item=known.get(id);
        if(!item) continue;
        newChoices[id]={decision:v.decision,reviewed_at:String(v.reviewed_at||'')};
        if(LIVE_MODE) {
          if(!item.fingerprint)continue;
          updates.push({id:Number(id),decision:v.decision,fingerprint:item.fingerprint});
        }
      }
      if(LIVE_MODE && updates.length) {
        const prefix=location.pathname.endsWith('/') ?
          location.pathname.slice(0,-1):location.pathname;
        const response=await fetch(prefix+'/api/import',{
          method:'POST',mode:'same-origin',credentials:'omit',
          headers:{'Content-Type':'application/json',
                   'X-Photo-Review':'manual-user-gesture'},
          body:JSON.stringify({updates})
        });
        const answer=await response.json();
        if(!response.ok||!answer.saved)throw Error('server persistence failed');
        document.getElementById('saveState').textContent=
          '✓ Импорт сохранён на телефоне ('+answer.count+')';
      }
      Object.assign(choices,newChoices);
      if (!LIVE_MODE) {
        try {localStorage.setItem(KEY,JSON.stringify(choices));}catch(_){}
      }
      redraw();
      populatePage();
    }catch(_){alert('Импорт не выполнен. Проверь файл разметки и соединение с локальным сервером.');}
  };r.readAsText(input.files[0]);
}
function movePage(delta) {
  const count=shownItems().length;
  currentPage = Math.max(1,Math.min(Math.max(1,Math.ceil(count/PAGE_SIZE)),currentPage+delta));
  populatePage();
  window.scrollTo(0,0);
}
function populatePage() {
  const holder=document.getElementById('tiles');
  holder.replaceChildren();
  const begin=(currentPage-1)*PAGE_SIZE;
  const visible=shownItems();
  visible.slice(begin,begin+PAGE_SIZE).forEach(item=>{
    const card=document.createElement('article');
    card.className='tile';card.dataset.id=String(item.id);
    const image=document.createElement('img');
    image.loading='lazy';image.alt='Фото №'+item.id;
    image.src='http://127.0.0.1:18780/api/thumb?id='+item.id;
    card.appendChild(image);
    const caption=document.createElement('div');
    caption.className='caption';
    const title=document.createElement('b');title.textContent='№ '+item.id+' · '+item.album;
    const desc=document.createElement('p');
    desc.textContent=item.name+' · метка '+item.score.toFixed(2);
    caption.append(title,desc);
    if (item.duplicate_review?.same_image_bytes) {
      const note=document.createElement('p');note.className='exact-copy';
      note.textContent='🔁 Точная копия ранее отмеченного изображения №'+
        item.duplicate_review.same_bytes_as_reviewed_photo_ids.join(', ')+
        ' — не распознавание лица';
      caption.appendChild(note);
    }
    card.appendChild(caption);
    const buttons=document.createElement('div');buttons.className='buttons';
    for (const [label,code] of [['✅ Это я','mine'],['Не я','other'],
                                ['В альбоме','in_album'],['Снять отметку','clear']]) {
      const button=document.createElement('button');button.textContent=label;
      button.addEventListener('click',()=>mark(item.id,code));
      buttons.appendChild(button);
    }
    card.appendChild(buttons);holder.appendChild(card);
  });
  document.getElementById('pageInfo').textContent =
    'Страница '+currentPage+' из '+Math.max(1,Math.ceil(visible.length/PAGE_SIZE))+
    ' · найдено '+visible.length;
  document.getElementById('prevPage').disabled=currentPage<=1;
  document.getElementById('nextPage').disabled=currentPage*PAGE_SIZE>=visible.length;
  redraw();
}
window.addEventListener('DOMContentLoaded',populatePage);
</script>"""
    script=(script.replace("__REVIEW_DATA__",dataset)
                  .replace("__REVIEW_PAGE_SIZE__",str(page_size))
                  .replace("__REVIEW_PAGE_NUMBER__",str(page))
                  .replace("__REVIEW_SCOPE__",scope)
                  .replace("__LIVE_MODE__","true" if live_mode else "false")
                  .replace("__PRIOR_REVIEWS__",json.dumps(
                      {str(k):v for k,v in report.get("reviewed_decisions",{}).items()},
                      separators=(",",":"))))
    style = """<style>
body{font-family:system-ui,sans-serif;background:#13171e;color:#f4f5f6;margin:0;padding:18px}
h1{font-size:1.25rem}.note{max-width:1100px;line-height:1.5;color:#c3cbd7}
.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:15px 0}
button,.action,select,input{border:1px solid #718095;border-radius:7px;background:#24344a;color:white;padding:8px}
button,.action{cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:12px}
.tile{background:#202936;border:1px solid #3a4352;border-radius:11px;padding:8px;overflow:hidden}
.tile img{width:100%;aspect-ratio:1;object-fit:contain;background:#0c0c0c}
.caption{font-size:0.77rem;overflow-wrap:anywhere;min-height:66px}.caption p{color:#aebccc}
.buttons{display:flex;gap:3px;flex-wrap:wrap}.buttons button{font-size:.72rem;padding:6px}
.tile[data-decision=mine]{outline:3px solid #30b89e}
.tile[data-decision=other]{opacity:.55}.tile[data-decision=in_album]{outline:3px solid #d9aa3e}
.exact-copy{color:#66dfad;font-weight:bold}
</style>"""
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'img-src http://127.0.0.1:18780; style-src &#39;unsafe-inline&#39;; '
        + ('connect-src &#39;self&#39;; ' if live_mode else '')
        + 'script-src &#39;unsafe-inline&#39;">'
        f'<title>Проверка фото вне альбома — страница {page}</title>{style}'
        '<h1>Фотографии людей для ручной проверки</h1>'
        f'<p class="note">{escape(details)}</p>'
        f'<p class="note">{escape(flags)}</p>'
        '<div class="actions"><button onclick="exportReviews()">Экспортировать отметки</button>'
        '<label class="action">Импортировать отметки <input type="file" '
        'accept=".json" onchange="importReviews(this)"></label>'
        '<span>Отмечено: <b id="marked">0</b></span>'
        '<span id="saveState" role="status"></span></div>'
        '<div class="actions"><label>Показывать '
        '<select id="filterMode" onchange="applyFilters()">'
        '<option value="all">Все</option><option value="unreviewed">Не просмотренные</option>'
        '<option value="mine">Это я</option><option value="other">Не я</option>'
        '<option value="in_album">В альбоме</option>'
        '<option value="duplicates">Точные копии</option></select></label>'
        '<input id="searchText" placeholder="Поиск по папке или имени" '
        'oninput="applyFilters()" autocomplete="off">'
        '</div>'
        '<div class="actions"><button id="prevPage" onclick="movePage(-1)">← Назад</button>'
        '<strong id="pageInfo"></strong>'
        '<button id="nextPage" onclick="movePage(1)">Вперёд →</button></div>'
        f'<main id="tiles" class="grid">{"".join(cards)}</main>{script}</html>'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    state = Path.home()/".termux-mcp-bridge/addons/photo-intelligence/state"
    ap.add_argument("--database",type=Path,default=state/"gallery.sqlite3")
    ap.add_argument("--exclude-album",default="",help="EXACT relative local album folder, not cloud album name")
    ap.add_argument("--exclude-manifest",type=Path,
                    help='JSON {"paths": ["relative/image.jpg", ...]} of exact album members')
    ap.add_argument("--min-score",type=float,default=0.19)
    ap.add_argument("--reviews",type=Path,
                    help="Your manual-review JSON export; verify scope and exact file copies")
    ap.add_argument("--no-auto-reviews",action="store_true",
                    help="Do not inspect latest exported review JSON in shared Download")
    ap.add_argument("--no-saved-reviews",action="store_true",
                    help="Ignore private durable manual review history for this run")
    ap.add_argument("--include-noise",action="store_true",help="include stickers/thumbnails")
    ap.add_argument("--page",type=int,default=1)
    ap.add_argument("--page-size",type=int,default=96)
    ap.add_argument("--serve",action="store_true",
                    help="Loopback browser review with immediate durable private saves")
    ap.add_argument("--port",type=int,default=0,
                    help="Loopback HTTP port for --serve (0 = OS-selected)")
    ap.add_argument("--output-dir",type=Path,default=state/"forensics")
    opts=ap.parse_args()
    report=candidates(opts.database,album=opts.exclude_album,
                      manifest=load_manifest(opts.exclude_manifest),
                      min_score=opts.min_score,include_noise=opts.include_noise)
    reviewed_path=opts.reviews
    if reviewed_path is None and not opts.no_auto_reviews:
        reviewed_path,found_reviews=find_exported_reviews(
            Path("/storage/emulated/0/Download"),report["review_scope"])
    elif reviewed_path is not None:
        found_reviews=read_reviews(reviewed_path,report["review_scope"])
    else:
        found_reviews={}
    from gallery_review_server import validated_state_decisions
    saved={} if opts.no_saved_reviews else validated_state_decisions(report,state)
    # Durable manual decisions are the source of truth, except when an
    # explicitly user-supplied JSON export overrides a previous choice.
    combined={**saved, **found_reviews}
    if combined:
        report=annotate_exact_copies(report,opts.database,combined)
    if not 1 <= opts.page_size <= 200 or opts.page < 1:
        ap.error("invalid page options")
    if opts.serve:
        from gallery_review_server import serve
        return serve(report,state,opts.page_size,opts.port)
    output=opts.output_dir
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    output.chmod(0o700)
    slug=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base=f"person-review-{slug}-p{opts.page}"
    result_path=output/(base+".html")
    html=render_html(report,page=opts.page,page_size=opts.page_size)
    fd=os.open(result_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as file:file.write(html)
    # No filenames or image bytes printed to terminal.
    print(json.dumps({
        "ok":True,"review_html":str(result_path),
        "candidate_count":report["candidate_count"],
        "page":opts.page,"shown":len(report["candidates"][
            (opts.page-1)*opts.page_size:opts.page*opts.page_size]),
        "generic_hits":report["counts"]["generic_person_hits"],
        "reference_album_visible":report["reference_album_visible"],
        "album_membership_known":report["album_membership_known"],
        "reference_album_indexed_count":report["reference_album_indexed_count"],
        "photos_modified":False,"identity_matched":False,
        "original_image_bytes_saved":False,
        "source_photos_opened": bool(reviewed_path and
                                   report.get("feedback_stats",{}).get("hash_bytes_read",0)),
        "review_export_loaded":reviewed_path is not None,
        "private_saved_reviews_loaded":len(saved),
        "reviewed_decisions":len(report.get("reviewed_decisions",{})),
        "exact_duplicate_candidates":len(report.get("exact_file_copies",{})),
        "biometric_identification":False,
    },ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
