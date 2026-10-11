"""Image decoder failures must not starve valid local gallery indexing.
Author: @dvkrum
"""
import os
import unittest

import numpy as np
from PIL import Image, UnidentifiedImageError

from test_addon_features import AddonFeatureTests
import gallery_semantic


class DecoderFailureTests(unittest.TestCase):
    def setUp(self):
        AddonFeatureTests.setUp(self)
        self.bad = self.g.SHARED_ROOT / "DCIM" / "Camera" / "undecodable.webp"
        self.bad.write_bytes(b"not a valid image data stream")
        self.assertTrue(self.g.gallery_scan({})["ok"])
        self.bad_id = next(
            item["id"] for item in self.g.gallery_list({"limit": 25})["items"]
            if item["name"] == self.bad.name
        )

    def repair_sample(self):
        Image.new("RGB", (42, 37), "blue").save(self.bad, format="WEBP")
        old_mtime=self.bad.stat().st_mtime
        os.utime(self.bad, (old_mtime+3,old_mtime+3))
        self.assertTrue(self.g.gallery_scan({})["ok"])

    def test_visual_retries_only_after_source_change(self):
        fn=self.g.TOOLS["gallery_visual_index"][1]
        first=fn({"ids":[self.bad_id]})
        self.assertEqual(first["errors"][0]["error"],"UnidentifiedImageError")
        report=self.g.TOOLS["gallery_visual_status"][1]({})
        self.assertEqual(report["failed"],1)
        self.assertEqual(report["local"]["remaining"],3)
        second=fn({"limit":20})
        self.assertEqual(second["errors"],[])
        self.assertNotIn(self.bad_id,second["indexed_ids"])
        self.repair_sample()
        updated=fn({"ids":[self.bad_id]})
        self.assertIn(self.bad_id,updated["indexed_ids"])
        self.assertEqual(self.g.TOOLS["gallery_visual_status"][1]({})["failed"],0)

    def test_semantic_skips_unchanged_errors_but_can_retry(self):
        original_available=gallery_semantic.available
        original_embed=gallery_semantic.image_embedding
        gallery_semantic.available=lambda:True
        def fake_embed(g,row):
            if row["name"]==self.bad.name:
                with Image.open(self.bad) as image:
                    image.verify()
            return np.ones(512,dtype=np.float32)
        gallery_semantic.image_embedding=fake_embed
        def restore():
            gallery_semantic.available=original_available
            gallery_semantic.image_embedding=original_embed
        self.addCleanup(restore)
        fn=self.g.TOOLS["gallery_semantic_index"][1]
        first=fn({"ids":[self.bad_id]})
        self.assertEqual(first["errors"][0]["error"],"UnidentifiedImageError")
        report=self.g.TOOLS["gallery_semantic_status"][1]({})
        self.assertEqual(report["failed"],1)
        second=fn({"limit":20})
        self.assertEqual(second["errors"],[])
        self.assertNotIn(self.bad_id,second["indexed_ids"])
        self.repair_sample()
        updated=fn({"ids":[self.bad_id]})
        self.assertIn(self.bad_id,updated["indexed_ids"])
        self.assertEqual(self.g.TOOLS["gallery_semantic_status"][1]({})["failed"],0)


if __name__=="__main__":
    unittest.main()
