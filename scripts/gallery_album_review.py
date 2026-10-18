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


NOISE = ("stickers", ".thumbnails", "gif_preview_cache")


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


def render_html(report: dict, *, page: int, page_size: int) -> str:
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
         "score":float(x["person_photo_score"])}
        for x in report["candidates"]
    ],ensure_ascii=True,separators=(",",":"))
    dataset = (dataset.replace("&",r"\u0026").replace("<",r"\u003c")
              .replace(">",r"\u003e"))
    scope = hashlib.sha256(
        ((report.get("reference_album") or "[no-album]")
         + "|" + report.get("manifest_fingerprint","")).encode("utf-8")
    ).hexdigest()[:16]
    script = """<script>
const ALL = __REVIEW_DATA__;
const PAGE_SIZE = __REVIEW_PAGE_SIZE__;
let currentPage = __REVIEW_PAGE_NUMBER__;
const KEY = 'photo-review-v1:__REVIEW_SCOPE__';
let choices = {};
try { choices = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (_) {}
function redraw() {
  document.querySelectorAll('article[data-id]').forEach(card => {
    const state = choices[card.dataset.id]?.decision || '';
    card.dataset.decision = state;
  });
  document.getElementById('marked').textContent = Object.keys(choices).length;
}
function mark(id, decision) {
  choices[id] = { decision, reviewed_at: new Date().toISOString() };
  try { localStorage.setItem(KEY, JSON.stringify(choices)); } catch (_) {}
  redraw();
}
function exportReviews() {
  const payload = JSON.stringify({schema:1,kind:'manual-photo-review',
      decisions:choices, verified_identity_by_model:false},null,2);
  const a = document.createElement('a');
  const url = URL.createObjectURL(new Blob([payload],{type:'application/json'}));
  a.href=url; a.download='photo-review-selections.json';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),2000);
}
function importReviews(input) {
  if (!input.files?.[0])return;
  const r = new FileReader();r.onload=()=>{
    try {const x=JSON.parse(r.result);
      if(x.schema!==1||!x.decisions||typeof x.decisions!=='object')throw Error('format');
      const allowed=['mine','other','in_album'];
      for(const [id,v] of Object.entries(x.decisions)) {
        if(!/^[0-9]+$/.test(id)||!allowed.includes(v?.decision))continue;
        choices[id]={decision:v.decision,reviewed_at:String(v.reviewed_at||'')};
      }
      try {localStorage.setItem(KEY,JSON.stringify(choices));}catch(_){}
      redraw();
    }catch(_){alert('Не удалось прочитать файл разметки');}
  };r.readAsText(input.files[0]);
}
function movePage(delta) {
  currentPage = Math.max(1,Math.min(Math.ceil(ALL.length/PAGE_SIZE),currentPage+delta));
  populatePage();
  window.scrollTo(0,0);
}
function populatePage() {
  const holder=document.getElementById('tiles');
  holder.replaceChildren();
  const begin=(currentPage-1)*PAGE_SIZE;
  ALL.slice(begin,begin+PAGE_SIZE).forEach(item=>{
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
    caption.append(title,desc);card.appendChild(caption);
    const buttons=document.createElement('div');buttons.className='buttons';
    for (const [label,code] of [['✅ Это я','mine'],['Не я','other'],['В альбоме','in_album']]) {
      const button=document.createElement('button');button.textContent=label;
      button.addEventListener('click',()=>mark(item.id,code));
      buttons.appendChild(button);
    }
    card.appendChild(buttons);holder.appendChild(card);
  });
  document.getElementById('pageInfo').textContent =
    'Страница '+currentPage+' из '+Math.max(1,Math.ceil(ALL.length/PAGE_SIZE));
  document.getElementById('prevPage').disabled=currentPage<=1;
  document.getElementById('nextPage').disabled=currentPage*PAGE_SIZE>=ALL.length;
  redraw();
}
window.addEventListener('DOMContentLoaded',populatePage);
</script>"""
    script=(script.replace("__REVIEW_DATA__",dataset)
                  .replace("__REVIEW_PAGE_SIZE__",str(page_size))
                  .replace("__REVIEW_PAGE_NUMBER__",str(page))
                  .replace("__REVIEW_SCOPE__",scope))
    style = """<style>
body{font-family:system-ui,sans-serif;background:#13171e;color:#f4f5f6;margin:0;padding:18px}
h1{font-size:1.25rem}.note{max-width:1100px;line-height:1.5;color:#c3cbd7}
.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:15px 0}
button,.action{cursor:pointer;border:1px solid #718095;border-radius:7px;background:#24344a;color:white;padding:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:12px}
.tile{background:#202936;border:1px solid #3a4352;border-radius:11px;padding:8px;overflow:hidden}
.tile img{width:100%;aspect-ratio:1;object-fit:contain;background:#0c0c0c}
.caption{font-size:0.77rem;overflow-wrap:anywhere;min-height:66px}.caption p{color:#aebccc}
.buttons{display:flex;gap:3px;flex-wrap:wrap}.buttons button{font-size:.72rem;padding:6px}
.tile[data-decision=mine]{outline:3px solid #30b89e}
.tile[data-decision=other]{opacity:.55}.tile[data-decision=in_album]{outline:3px solid #d9aa3e}
</style>"""
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'img-src http://127.0.0.1:18780; style-src &#39;unsafe-inline&#39;; '
        'script-src &#39;unsafe-inline&#39;">'
        f'<title>Проверка фото вне альбома — страница {page}</title>{style}'
        '<h1>Фотографии людей для ручной проверки</h1>'
        f'<p class="note">{escape(details)}</p>'
        f'<p class="note">{escape(flags)}</p>'
        '<div class="actions"><button onclick="exportReviews()">Экспортировать отметки</button>'
        '<label class="action">Импортировать отметки <input type="file" '
        'accept=".json" onchange="importReviews(this)"></label>'
        '<span>Отмечено: <b id="marked">0</b></span></div>'
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
    ap.add_argument("--include-noise",action="store_true",help="include stickers/thumbnails")
    ap.add_argument("--page",type=int,default=1)
    ap.add_argument("--page-size",type=int,default=96)
    ap.add_argument("--output-dir",type=Path,default=state/"forensics")
    opts=ap.parse_args()
    report=candidates(opts.database,album=opts.exclude_album,
                      manifest=load_manifest(opts.exclude_manifest),
                      min_score=opts.min_score,include_noise=opts.include_noise)
    if not 1 <= opts.page_size <= 200 or opts.page < 1:
        ap.error("invalid page options")
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
        "original_image_bytes_saved":False,"source_photos_opened":False,
    },ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
