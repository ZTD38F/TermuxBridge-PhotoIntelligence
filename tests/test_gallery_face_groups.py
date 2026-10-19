"""Synthetic face grouping and isolated state; never use real user photos."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

import numpy as np

SPEC=importlib.util.spec_from_file_location(
    "face_groups",Path(__file__).resolve().parents[1]/"scripts/gallery_face_groups.py")
assert SPEC and SPEC.loader
m=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class FaceGroupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="photo-face-test-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.media=self.root/"media"
        self.media.mkdir()
        self.dbpath=self.root/"private"/"faces.sqlite3"
        self.src=self.root/"gallery.sqlite3"
        c=sqlite3.connect(self.src)
        c.execute("CREATE TABLE images (id INTEGER PRIMARY KEY,path TEXT,size INTEGER,mtime REAL)")
        (self.media/"Camera").mkdir()
        sample=self.media/"Camera/a.jpg"
        sample.write_bytes(b"not an image")
        info=sample.stat()
        c.execute("INSERT INTO images VALUES (?,?,?,?)",(1,"Camera/a.jpg",info.st_size,info.st_mtime))
        c.execute("INSERT INTO images VALUES (?,?,?,?)",(2,"shizuku:///Android/data/a.jpg",5,123))
        c.commit();c.close()

    def test_mislabeled_zip_as_webp_is_not_a_photo(self):
        archive=self.media/"Camera/sticker.webp"
        archive.write_bytes(b"PK\x03\x04" + b"not real image" * 4)
        self.assertEqual(m.nonimage_archive_header(archive),"ZIP_SIGNATURE")
        self.assertIsNone(m.nonimage_archive_header(self.media/"Camera/a.jpg"))

    def test_old_decoder_errors_are_reclassified_without_media_mutation(self):
        archive=self.media/"Camera/renamed.webp"
        archive.write_bytes(b"PK\x03\x04" + b"zipped pretend webp")
        info=archive.stat()
        c=m.setup_private_db(self.dbpath)
        with c:
            c.execute("""INSERT INTO face_scans
              (image_id,path,size,mtime,algorithm,status,checked_at,error_code)
              VALUES(?,?,?,?,?,?,?,?)""",
              (45,"Camera/renamed.webp",info.st_size,info.st_mtime,m.VERSION,
               "ERROR",m.now(),"UnidentifiedImageError"))
        original=archive.read_bytes()
        n=m.reconcile_existing_mislabeled_archives(c,self.media)
        self.assertEqual(n,1)
        self.assertEqual(c.execute("select status from face_scans where image_id=45").fetchone()[0],
                         "SKIPPED_NONIMAGE")
        self.assertEqual(archive.read_bytes(),original)
        c.close()

    def test_private_file_mode_and_schema(self):
        c=m.setup_private_db(self.dbpath)
        self.assertEqual(self.dbpath.stat().st_mode&0o777,0o600)
        self.assertEqual(self.dbpath.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(c.execute("select count(*) from face_embeddings").fetchone()[0],0)
        c.close()

    def test_skip_android_data_virtual_source_and_resumption(self):
        c=m.setup_private_db(self.dbpath)
        candidates=list(m.iter_pending(self.src,c,10,self.media))
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0][0],1)
        with c:
            c.execute("""INSERT INTO face_scans(image_id,path,size,mtime,algorithm,status,
                     face_count,checked_at) VALUES(?,?,?,?,?,?,?,?)""",
                     (1,"Camera/a.jpg",candidates[0][2],candidates[0][3],m.VERSION,"DONE",0,m.now()))
        self.assertEqual(list(m.iter_pending(self.src,c,10,self.media)),[])
        c.close()

    def test_safe_path_traversal_symlink_rejected(self):
        self.assertIsNone(m._safepath(self.media,"../etc/passwd"))
        self.assertIsNone(m._safepath(self.media,"/absolute.jpg"))
        self.assertIsNone(m._safepath(self.media,"Camera/../a.jpg"))
        (self.media/"Camera/link").symlink_to(self.src)
        self.assertIsNone(m._safepath(self.media,"Camera/link"))

    def test_nms_overlapping_suppressed_distant_kept(self):
        boxes=np.array([[10,10,80,80],[15,15,81,81],[300,300,90,90]],dtype=float)
        keep=m.nms(boxes,[.97,.86,.88])
        self.assertEqual(keep,[0,2])

    def test_yunet_decode_center_and_landmarks(self):
        shape={8:6400,16:1600,32:400}
        outputs={}
        for stride,count in shape.items():
            outputs.update({f"cls_{stride}":np.zeros((1,count,1),dtype=np.float32),
                            f"obj_{stride}":np.zeros((1,count,1),dtype=np.float32),
                            f"bbox_{stride}":np.zeros((1,count,4),dtype=np.float32),
                            f"kps_{stride}":np.zeros((1,count,10),dtype=np.float32)})
        i=11*80+12
        outputs["cls_8"][0,i,0]=.95
        outputs["obj_8"][0,i,0]=.95
        outputs["bbox_8"][0,i,:]=[0,0,2,2]
        faces=m.decode_yunet(outputs)
        self.assertEqual(len(faces),1)
        bounds,landmarks,score=faces[0]
        self.assertGreater(score,.9)
        self.assertEqual(landmarks.shape,(5,2))
        self.assertAlmostEqual(float(bounds[0]+bounds[2]/2),12*8)
        self.assertAlmostEqual(float(bounds[1]+bounds[3]/2),11*8)

    def test_sface_affine_alignment_pil(self):
        from PIL import Image
        arr=np.zeros((640,640,3),dtype=np.uint8)
        landmarks=np.array(m.TEMPLATE,dtype=np.float32)+50
        face=m.aligned_face(arr,landmarks)
        self.assertEqual(face.size,(112,112))

    def _add_embedding(self,c,image_id,vector):
        v=np.asarray(vector,dtype=np.float32)
        v=v/np.linalg.norm(v)
        c.execute("""INSERT INTO face_embeddings(image_id,face_ordinal,x,y,width,height,
                    score,vector,algorithm) VALUES(?,?,?,?,?,?,?,?,?)""",
                  (image_id,0,1,2,64,65,.93,v.tobytes(),m.VERSION))

    def test_conservative_grouping_and_metadata(self):
        c=m.setup_private_db(self.dbpath)
        a=np.zeros(128);a[0]=1
        b=np.zeros(128);b[1]=1
        with c:
            self._add_embedding(c,1,a)
            self._add_embedding(c,2,a+.01*b)
            self._add_embedding(c,3,b)
        c.close()
        result=m.group_embeddings(self.dbpath)
        self.assertEqual(result["faces"],3)
        self.assertEqual(result["groups"],2)
        self.assertEqual(result["groups_with_2plus"],1)
        con=m.sqlite_ro(self.dbpath)
        groups=dict(con.execute("select face_id,cluster_id from face_clusters"))
        self.assertEqual(groups[1],groups[2])
        self.assertNotEqual(groups[2],groups[3])
        con.close()

    def test_not_group_unrelated_ambiguous_vectors(self):
        c=m.setup_private_db(self.dbpath)
        basis=np.eye(128,dtype=np.float32)
        with c:
            self._add_embedding(c,1,basis[0])
            self._add_embedding(c,2,basis[1])
            self._add_embedding(c,3,basis[2])
        c.close()
        result=m.group_embeddings(self.dbpath)
        self.assertEqual(result["groups"],3)

    def test_private_gallery_escaping_and_loopback_only(self):
        data={"scanned_images":5,"indexed_faces":6,
              "groups":[{"id":1,"name":"<script>alert(1)</script>",
                         "photos":2,"photo_ids":[10,20]}]}
        p=m.write_private_gallery(data,self.dbpath.parent/"forensics")
        txt=p.read_text()
        self.assertNotIn("<script>",txt)
        self.assertIn("&lt;script&gt;",txt)
        self.assertIn("127.0.0.1:18780",txt)
        self.assertEqual(p.stat().st_mode&0o777,0o600)

    def test_wrong_model_checksum_rejected(self):
        folder=self.root/"models"
        folder.mkdir()
        for name in m.MODELS:
            (folder/name).write_bytes(b"not ONNX")
        with self.assertRaisesRegex(ValueError,"SHA256"):
            m.verify_models(folder)

    def test_status_readonly_progress_and_no_invented_names(self):
        initial=m.status_report(self.src,self.dbpath)
        self.assertEqual(initial["local_images"],1)
        self.assertEqual(initial["currently_scanned"],0)
        self.assertFalse(initial["person_names_inferred"])
        c=m.setup_private_db(self.dbpath)
        row=list(m.iter_pending(self.src,c,2,self.media))[0]
        with c:
            c.execute("""INSERT INTO face_scans
               (image_id,path,size,mtime,algorithm,status,checked_at)
               VALUES(?,?,?,?,?,?,?)""",
               (row[0],row[1],row[2],row[3],m.VERSION,"DONE",m.now()))
        c.close()
        x=m.status_report(self.src,self.dbpath)
        self.assertEqual(x["currently_scanned"],1)
        self.assertEqual(x["remaining"],0)
        self.assertEqual(x["database_integrity"],"ok")

    def test_source_untouched_for_grouping_and_report(self):
        old=self.src.read_bytes()
        c=m.setup_private_db(self.dbpath);c.close()
        m.group_embeddings(self.dbpath)
        self.assertEqual(self.src.read_bytes(),old)


if __name__=="__main__":unittest.main()
