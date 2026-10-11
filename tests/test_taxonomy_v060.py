from __future__ import annotations
import unittest
from array import array
from tests.test_addon_features import AddonFeatureTests
import gallery_taxonomy
from gallery_semantic import MODEL_ID,DIM

class VisualTaxonomyV060Tests(AddonFeatureTests):
    def setUp(self):
        super().setUp()
        gallery_taxonomy.install(self.g.__dict__)
        item=self.g.gallery_list({"limit":20})["items"][0]
        self.item=item
        self.g.TOOLS["gallery_semantic_status"][1]({})
        emb=array("f",[1.0]+[0.0]*(DIM-1)).tobytes()
        with self.g._db() as c:
            c.execute("""INSERT OR REPLACE INTO gallery_semantic(image_id,size,mtime,model,processed_at,embedding,error)
              VALUES(?,?,?,?,?,?,NULL)""",(item["id"],item["size"],item["mtime"],MODEL_ID,1234.0,emb))
        self.old_vectors=gallery_taxonomy.label_vectors
        document=array("f",[1.0]+[0.0]*(DIM-1))
        food=array("f",[0.0,1.0]+[0.0]*(DIM-2))
        gallery_taxonomy.label_vectors=lambda:{"document":document,"food":food}
        self.addCleanup(self.restore_vectors)
    def restore_vectors(self):
        gallery_taxonomy.label_vectors=self.old_vectors

    def test_taxonomy_indexes_existing_embedding_without_identity(self):
        r=self.g.TOOLS["gallery_taxonomy_index"][1]({"limit":20,"top_k":2})
        self.assertTrue(r["ok"]);self.assertEqual(r["indexed_ids"],[self.item["id"]]);self.assertFalse(r["identity_inference"])
        got=self.g.TOOLS["gallery_taxonomy_get"][1]({"id":self.item["id"]})
        self.assertEqual(got["labels"][0]["label"],"document");self.assertGreater(got["labels"][0]["score"],got["labels"][1]["score"]);self.assertFalse(got["identity_inference"])

    def test_taxonomy_search(self):
        self.g.TOOLS["gallery_taxonomy_index"][1]({"limit":20,"top_k":2})
        r=self.g.TOOLS["gallery_taxonomy_search"][1]({"label":"document","min_score":0.5,"limit":10})
        self.assertEqual(r["count"],1);self.assertEqual(r["items"][0]["id"],self.item["id"]);self.assertFalse(r["identity_inference"])

    def test_taxonomy_status_tracks_semantic_eligibility(self):
        before=self.g.TOOLS["gallery_taxonomy_status"][1]({})
        self.assertEqual(before["eligible"],1);self.assertEqual(before["remaining"],1);self.assertFalse(before["identity_inference"])
        self.g.TOOLS["gallery_taxonomy_index"][1]({"limit":20})
        after=self.g.TOOLS["gallery_taxonomy_status"][1]({})
        self.assertEqual(after["indexed"],1);self.assertEqual(after["remaining"],0)

    def test_taxonomy_label_catalog_is_generic(self):
        r=self.g.TOOLS["gallery_taxonomy_labels"][1]({})
        names={x["label"] for x in r["labels"]}
        self.assertIn("person",names);self.assertIn("document",names);self.assertNotIn("identity",names);self.assertFalse(r["identity_inference"])
