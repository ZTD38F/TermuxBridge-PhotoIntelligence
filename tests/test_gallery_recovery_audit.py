"""No user photos touched: synthetic read-only recovery-audit tests."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

S=importlib.util.spec_from_file_location(
    "recover",Path(__file__).resolve().parents[1]/"scripts/gallery_recovery_audit.py")
assert S and S.loader
m=importlib.util.module_from_spec(S)
S.loader.exec_module(m)


def create_db(path,rows):
    c=sqlite3.connect(path)
    c.execute("create table images(path text,size integer,mtime real,name text)")
    c.executemany("insert into images values(?,?,?,?)",rows)
    c.commit();c.close()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory(prefix="photo-recovery-fixture-")
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)/"shared"
        (self.root/"DCIM/Camera").mkdir(parents=True)
        (self.root/"Pictures").mkdir()
        (self.root/"DCIM"/"same.jpg").write_bytes(b"hello")
        self.old=Path(d.name)/"old.sqlite"
        self.new=Path(d.name)/"current.sqlite"
        timestamp=(self.root/"DCIM"/"same.jpg").stat().st_mtime
        create_db(self.old,[
            ("Pictures/same.jpg",5,timestamp,"same.jpg"),
            ("DCIM/Camera/lost.jpg",8,100.,"lost.jpg"),
            ("shizuku://x/old.jpg",3,100.,"old.jpg")])
        create_db(self.new,[
            ("DCIM/same.jpg",5,timestamp,"same.jpg"),
            ("shizuku://x/old.jpg",3,100.,"old.jpg")])

    def test_correct_non_destructive_classification(self):
        r=m.audit(self.old,self.new,self.root)
        self.assertEqual((r["missing_index_rows"],r["protected_rows_missing"]),(2,0))
        self.assertEqual(r["probable_relocations"],1)
        self.assertEqual(r["unresolved_paths"],1)
        self.assertEqual(r["missing_camera_paths"],1)
        self.assertFalse(r["image_bytes_read"])
        self.assertFalse(r["photos_modified"])

    def test_original_not_touched(self):
        target=self.root/"DCIM/same.jpg"
        before=target.read_bytes(),target.stat().st_mtime_ns
        m.audit(self.old,self.new,self.root)
        self.assertEqual(before,(target.read_bytes(),target.stat().st_mtime_ns))

    def test_path_traversal_and_symlink_escape_denied(self):
        for p in ("../secret","A/../data","/etc/passwd",""):
            self.assertIsNone(m.safe_path(self.root,p))
        (self.root/"Pictures/shortcut").symlink_to(self.old)
        self.assertIsNone(m.safe_path(self.root,"Pictures/shortcut"))

    def test_no_mutation_of_either_db(self):
        orig=self.old.read_bytes(),self.new.read_bytes()
        m.audit(self.old,self.new,self.root)
        self.assertEqual(orig,(self.old.read_bytes(),self.new.read_bytes()))

    def test_bad_db_fails_closed(self):
        self.old.write_text("invalid")
        with self.assertRaises(sqlite3.DatabaseError):
            m.audit(self.old,self.new,self.root)

if __name__=="__main__":unittest.main()
