from __future__ import annotations
import unittest
from tests.test_addon_features import AddonFeatureTests
import gallery_metadata,gallery_smart_albums

class MetadataAndSmartAlbumsV050Tests(AddonFeatureTests):
    def setUp(self):
        super().setUp()
        gallery_metadata.install(self.g.__dict__)
        gallery_smart_albums.install(self.g.__dict__)

    def test_metadata_index_and_privacy_default(self):
        result=self.g.TOOLS["gallery_metadata_index"][1]({"limit":10})
        self.assertTrue(result["ok"]);self.assertEqual(len(result["indexed_ids"]),3)
        status=self.g.TOOLS["gallery_metadata_status"][1]({})
        self.assertEqual(status["indexed"],3);self.assertEqual(status["remaining"],0)
        item=self.g.gallery_list({"limit":10})["items"][0]
        meta=self.g.TOOLS["gallery_metadata_get"][1]({"id":item["id"]})
        self.assertFalse(meta["gps_included"]);self.assertNotIn("gps_lat",meta);self.assertGreater(meta["width"],0);self.assertGreater(meta["height"],0)

    def test_metadata_orientation_query(self):
        self.g.TOOLS["gallery_metadata_index"][1]({"limit":10})
        portrait=self.g.TOOLS["gallery_metadata_query"][1]({"orientation":"portrait","limit":20})
        landscape=self.g.TOOLS["gallery_metadata_query"][1]({"orientation":"landscape","limit":20})
        self.assertTrue(any(x["name"]=="screenshot_1.png" for x in portrait["items"]))
        self.assertTrue(any(x["name"]=="black.jpg" for x in landscape["items"]))

    def test_virtual_tags_roundtrip(self):
        item=self.g.gallery_list({"limit":10})["items"][0];source=self.g.SHARED_ROOT/item["path"];before=source.stat()
        add=self.g.TOOLS["gallery_tag_add"][1]({"ids":[item["id"]],"tag":"Travel"})
        self.assertTrue(add["ok"]);self.assertFalse(add["source_files_modified"])
        tags=self.g.TOOLS["gallery_tags"][1]({});self.assertTrue(any(x["tag"]=="Travel" and x["count"]==1 for x in tags["tags"]))
        listed=self.g.TOOLS["gallery_tag_items"][1]({"tag":"Travel"});self.assertEqual([x["id"] for x in listed["items"]],[item["id"]])
        rem=self.g.TOOLS["gallery_tag_remove"][1]({"ids":[item["id"]],"tag":"Travel"});self.assertEqual(rem["removed"],1)
        after=source.stat();self.assertEqual((before.st_size,before.st_mtime_ns),(after.st_size,after.st_mtime_ns))

    def test_builtin_and_custom_smart_album(self):
        self.g.TOOLS["gallery_metadata_index"][1]({"limit":10})
        albums=self.g.TOOLS["gallery_smart_albums"][1]({})["albums"]
        self.assertTrue(any(x["album_id"]=="builtin:screenshots" and x["count"]>=1 for x in albums))
        created=self.g.TOOLS["gallery_smart_album_create"][1]({"name":"My Screens","criteria":{"path_contains":"screenshots"}})
        aid=created["album_id"];self.assertGreaterEqual(created["count"],1);self.assertFalse(created["source_files_modified"])
        items=self.g.TOOLS["gallery_smart_album_items"][1]({"album_id":aid,"limit":20})
        self.assertTrue(any(x["name"]=="screenshot_1.png" for x in items["items"]))
        with self.assertRaises(PermissionError):
            self.g.TOOLS["gallery_smart_album_delete"][1]({"album_id":aid,"confirm_album_id":"wrong"})
        deleted=self.g.TOOLS["gallery_smart_album_delete"][1]({"album_id":aid,"confirm_album_id":aid});self.assertTrue(deleted["deleted"])

    def test_tag_backed_smart_album(self):
        item=self.g.gallery_list({"limit":10})["items"][0]
        self.g.TOOLS["gallery_tag_add"][1]({"ids":[item["id"]],"tag":"Favorite"})
        created=self.g.TOOLS["gallery_smart_album_create"][1]({"name":"Favorites","criteria":{"tags_all":["Favorite"]}})
        items=self.g.TOOLS["gallery_smart_album_items"][1]({"album_id":created["album_id"]})
        self.assertEqual([x["id"] for x in items["items"]],[item["id"]])

    def test_builtin_album_cannot_be_deleted(self):
        with self.assertRaises(PermissionError):
            self.g.TOOLS["gallery_smart_album_delete"][1]({"album_id":"builtin:camera","confirm_album_id":"builtin:camera"})
