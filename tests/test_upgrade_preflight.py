"""Offline compatibility tests for the read-only upgrade preflight.
Author: @dvkrum
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.upgrade_preflight import check


class UpgradePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.installed=root/"installed"
        self.candidate=root/"candidate"
        self.bridge=root/"bridge"
        self.database=root/"state/gallery.sqlite3"
        self.installed.mkdir()
        self.candidate.mkdir()
        self.bridge.mkdir()
        (self.candidate/"runtime").mkdir()
        (self.installed/"runtime").mkdir()
        self.database.parent.mkdir()
        self.installed_manifest={"name":"photo-intelligence","version":"0.4.0","abi":1,"author":"@dvkrum"}
        self.target_manifest={"name":"photo-intelligence","version":"0.6.1","abi":1,
                              "author":"@dvkrum","bridge_min_version":"1.2.31"}
        (self.installed/"addon.json").write_text(json.dumps(self.installed_manifest))
        (self.candidate/"addon.json").write_text(json.dumps(self.target_manifest))
        for f in ("service.py","adapter.py","photo-intelligence"):
            (self.candidate/f).write_text("# test fixture\n")
        (self.candidate/"runtime/photo_config.py").write_text('ADDON_VERSION = "0.6.1"\n')
        (self.candidate/"runtime/db_migrations.py").write_text("TARGET_VERSION = 4\n")
        (self.bridge/"bridge_server.py").write_text('BRIDGE_VERSION = "1.2.33"\n')
        with sqlite3.connect(self.database) as c:
            c.execute("pragma user_version=4")
            c.execute("create table images(id integer primary key,path text)")
            c.execute("insert into images(path) values (?)",("synthetic.jpg",))

    def report(self):
        return check(installed=self.installed, candidate=self.candidate,
                     bridge_runtime=self.bridge, database=self.database,
                     minimum_free_mb=0)

    def test_matching_schema_and_minimum_version(self):
        r=self.report()
        self.assertTrue(r["ok"],r)
        self.assertEqual(r["db_integrity"],"ok")
        self.assertTrue(r["read_only"])
        with sqlite3.connect(self.database) as c:
            self.assertEqual(c.execute("select count(*) from images").fetchone()[0],1)

    def test_version_collision_is_blocked(self):
        self.target_manifest["version"]="0.4.0"
        (self.candidate/"addon.json").write_text(json.dumps(self.target_manifest))
        r=self.report()
        self.assertFalse(r["ok"])
        self.assertIn("CANDIDATE_NOT_NEWER",r["issues"])

    def test_schema_downgrade_is_blocked(self):
        (self.candidate/"runtime/db_migrations.py").write_text("TARGET_VERSION = 3\n")
        self.assertIn("UNSUPPORTED_SCHEMA_DOWNGRADE",self.report()["issues"])

    def test_bridge_too_old_blocks_upgrade(self):
        (self.bridge/"bridge_server.py").write_text('BRIDGE_VERSION = "1.2.20"\n')
        self.assertIn("BRIDGE_VERSION_TOO_OLD",self.report()["issues"])

    def test_bad_sqlite_is_rejected_without_changes(self):
        with self.database.open("wb") as f:f.write(b"invalid synthetic sqlite data")
        before=self.database.read_bytes()
        r=self.report()
        self.assertFalse(r["ok"])
        self.assertIn("DATABASE_READ_FAILED",r["issues"])
        self.assertEqual(self.database.read_bytes(),before)

    def test_incorrect_author_and_abi_are_rejected(self):
        self.target_manifest.update(author="other",abi=2)
        (self.candidate/"addon.json").write_text(json.dumps(self.target_manifest))
        r=self.report()
        self.assertIn("ABI_MISMATCH",r["issues"])
        self.assertIn("AUTHOR_MISMATCH",r["issues"])


if __name__=="__main__":
    unittest.main()
