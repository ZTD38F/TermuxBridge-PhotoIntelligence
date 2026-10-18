"""Metadata-only photo review: no biometric matching, no media writes."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location(
    "albumreview",Path(__file__).resolve().parents[1]/"scripts/gallery_album_review.py")
assert SPEC and SPEC.loader
review=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="album-review-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.images=self.root/"shared"
        self.images.mkdir()
        (self.images/"Camera").mkdir()
        (self.images/"My album").mkdir()
        files=[
            ("Camera/p1.jpg",b"abc"),("Camera/p2.jpg",b"1234"),
            ("My album/mine.jpg",b"56789"),("Camera/misc.jpg",b"abcd"),
            ("Camera/<script>.jpg",b"html"),("Camera/old.jpg",b"stale")
        ]
        for p,data in files:(self.images/p).write_bytes(data)
        self.db=self.root/"gallery.sqlite3"
        db=sqlite3.connect(self.db)
        db.executescript("""
        CREATE TABLE images (
          id INTEGER PRIMARY KEY,path TEXT,name TEXT,album TEXT,
          size INTEGER,mtime REAL,seen INTEGER
        );
        CREATE TABLE gallery_taxonomy (
          image_id INTEGER PRIMARY KEY,size INTEGER,mtime REAL,labels TEXT
        );
        """)
        for i,(p,data) in enumerate(files,1):
            stat=(self.images/p).stat()
            name=Path(p).name;album=str(Path(p).parent)
            db.execute("INSERT INTO images VALUES (?,?,?,?,?,?,?)",
                       (i,p,name,album,len(data),stat.st_mtime,1))
            labels=(
                [{"label":"person","score":0.40}] if i in (1,3,5,6) else
                [{"label":"people","score":0.35}] if i==2 else
                [{"label":"food","score":0.9}])
            stored_size=len(data)+(1 if i==6 else 0)
            db.execute("INSERT INTO gallery_taxonomy VALUES (?,?,?,?)",
                       (i,stored_size,stat.st_mtime,json.dumps(labels)))
        db.commit();db.close()

    def get(self,**options):
        return review.candidates(self.db,shared_root=self.images,**options)

    def test_exact_album_exclusion_and_generic_person_filter(self):
        r=self.get(album="My album")
        self.assertTrue(r["reference_album_visible"])
        self.assertTrue(r["album_membership_known"])
        self.assertEqual(r["reference_album_indexed_count"],1)
        self.assertEqual(r["candidate_count"],3)
        self.assertEqual({x["id"] for x in r["candidates"]},{1,2,5})
        self.assertFalse(r["identity_recognition"])
        self.assertTrue(r["taxonomy_coverage_incomplete"])

    def test_cloud_album_name_does_not_falsely_exclude(self):
        r=self.get(album="Давид")
        self.assertFalse(r["reference_album_visible"])
        self.assertFalse(r["album_membership_known"])
        self.assertIsNone(r["reference_album_indexed_count"])
        self.assertEqual(r["candidate_count"],4)
        self.assertTrue(r["not_verified_same_person"])

    def test_manifest_matches_paths_not_basename(self):
        r=self.get(manifest={"Camera/p1.jpg"})
        self.assertEqual(r["candidate_count"],3)
        self.assertNotIn(1,{x["id"] for x in r["candidates"]})
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"list.json";p.write_text(json.dumps({"paths":["Camera/p2.jpg"]}))
            self.assertEqual(review.load_manifest(p),{"Camera/p2.jpg"})
            p.write_text(json.dumps({"paths":["../etc/passwd"]}))
            with self.assertRaises(ValueError):review.load_manifest(p)

    def test_rejects_symlinks_and_stale_taxonomy(self):
        (self.images/"Camera/p1.jpg").unlink()
        (self.images/"Camera/p1.jpg").symlink_to(self.db)
        r=self.get()
        self.assertEqual(r["candidate_count"],3)
        self.assertEqual(r["counts"]["files_unavailable"],1)

    def test_html_escapes_filenames_and_renders_manual_review_controls(self):
        r=self.get()
        page=review.render_html(r,page=1,page_size=20)
        self.assertIn("&lt;script&gt;",page)
        self.assertNotIn('<script>.jpg',page)
        self.assertIn("identity",page)
        self.assertIn('exportReviews()',page)
        self.assertIn('http://127.0.0.1:18780/api/thumb?id=',page)
        self.assertEqual(page.count('class="tile"'),4)
        self.assertIn("ALL.slice(begin,begin+PAGE_SIZE)",page)
        self.assertIn('id="nextPage"',page)
        self.assertIn('id="prevPage"',page)
        self.assertIn('const ALL = [',page)
        other=review.render_html(self.get(album="My album"),page=1,page_size=20)
        import re
        a=re.search("const KEY = '([^']+)'",page).group(1)
        b=re.search("const KEY = '([^']+)'",other).group(1)
        self.assertNotEqual(a,b)
        self.assertTrue(a.startswith('photo-review-v1:'))
        for p in (0,-1):
            with self.assertRaises(ValueError):
                review.render_html(r,page=p,page_size=10)

    def test_no_original_or_database_modification(self):
        before=self.db.read_bytes()
        files={str(p):p.read_bytes() for p in self.images.rglob("*.jpg")}
        self.get(album="My album")
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual({str(p):p.read_bytes() for p in self.images.rglob("*.jpg")},files)

    def test_empty_and_noise_and_validation(self):
        with self.assertRaises(ValueError):self.get(min_score=1.01)
        with self.assertRaises(ValueError):self.get(album="\x00")
        self.assertEqual(self.get(min_score=0.99)["candidate_count"],0)
        self.assertEqual(self.get()["candidate_count"],4)


if __name__=="__main__":unittest.main()
