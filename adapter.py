"""TermuxBridge addon entrypoint for Photo Intelligence.
Author: @dvkrum
"""
from __future__ import annotations
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT/"runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0,str(RUNTIME))
import gallery_bridge_tools as gallery
from photo_config import ADDON_VERSION

ADDON_METADATA={"name":"photo-intelligence","version":ADDON_VERSION,"abi":1,"bridge_major":1,"namespace":"gallery_","author":"@dvkrum"}
TOOLS=gallery.TOOLS
DESCRIPTIONS=gallery.DESCRIPTIONS
READ_ONLY=gallery.READ_ONLY
DESTRUCTIVE=getattr(gallery,"DESTRUCTIVE",set())
OPEN_WORLD=getattr(gallery,"OPEN_WORLD",set())
VIRTUAL_SCHEMES={"gallery":"virtual_read"}

def virtual_read(path: str, max_chars: int = 100000):
    parsed=urlparse(path)
    if parsed.scheme!="gallery": raise ValueError("unsupported virtual scheme")
    route=(parsed.netloc+parsed.path).strip("/"); query=parse_qs(parsed.query)
    one=lambda name,default=None: query.get(name,[default])[-1]
    if route.startswith("image/"):
        return gallery.gallery_get_image({"id":int(route.split("/",1)[1]),"max_edge":int(one("max_edge","3072")),"quality":int(one("quality","90"))})
    if route.startswith("thumbnail/"):
        return gallery.gallery_thumbnail({"id":int(route.split("/",1)[1]),"max_edge":int(one("max_edge","768"))})
    if route in {"contact-sheet","contact_sheet"}:
        return gallery.gallery_contact_sheet({"limit":int(one("limit","36")),"offset":int(one("offset","0")),"columns":int(one("columns","6")),"sort":one("sort","newest")})
    if route=="status": return gallery.gallery_status({})
    if route=="ocr/status": return gallery.gallery_ocr_status({})
    if route=="visual/status": return gallery.TOOLS["gallery_visual_status"][1]({})
    if route=="albums": return gallery.gallery_albums({"limit":int(one("limit","100")),"query":one("query","")})
    if route=="list": return gallery.gallery_list({"limit":int(one("limit","100")),"offset":int(one("offset","0")),"query":one("query","")})
    if route=="search": return gallery.gallery_search({"limit":int(one("limit","100")),"offset":int(one("offset","0")),"query":one("query","")})
    if route.startswith("ocr/text/"): return gallery.gallery_ocr_text({"id":int(route.rsplit("/",1)[1])})
    raise ValueError("unknown gallery virtual route")
