from __future__ import annotations
import tempfile,types,sys,unittest
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]/'runtime';sys.path.insert(0,str(BASE))
from PIL import Image
import gallery_intelligence,gallery_ocr,gallery_visual,gallery_organizer,gallery_cleanup,gallery_semantic,gallery_smart_search,gallery_duplicates

class AddonFeatureTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);base=Path(tmp.name);shared=base/'shared';state=base/'state';shared.mkdir();state.mkdir();
        (shared/'DCIM'/'Camera').mkdir(parents=True);(shared/'Pictures'/'Screenshots').mkdir(parents=True);(shared/'Android'/'media'/'demo'/'cache').mkdir(parents=True)
        Image.new('RGB',(320,180),'black').save(shared/'DCIM'/'Camera'/'black.jpg')
        Image.new('RGB',(180,320),'white').save(shared/'Pictures'/'Screenshots'/'screenshot_1.png')
        Image.new('RGB',(64,64),'red').save(shared/'Android'/'media'/'demo'/'cache'/'tiny.jpg')
        code=(BASE/'gallery_bridge_tools.py').read_text().split('\n# TermuxBridge Photo Intelligence v2')[0]
        m=types.ModuleType('addon_feature_sandbox');exec(compile(code,str(BASE/'gallery_bridge_tools.py'),'exec'),m.__dict__)
        m.SHARED_ROOT=shared;m.STATE_DIR=state;m.DB_PATH=state/'gallery.sqlite3'
        gallery_intelligence.install(m.__dict__);gallery_ocr.install(m.__dict__);gallery_visual.install(m.__dict__);gallery_organizer.install(m.__dict__);gallery_duplicates.install(m.__dict__);gallery_cleanup.install(m.__dict__);gallery_semantic.install(m.__dict__);gallery_smart_search.install(m.__dict__)
        self.g=m;self.assertTrue(m.gallery_scan({})['ok'])
    def test_visual_index_and_search(self):
        r=self.g.TOOLS['gallery_visual_index'][1]({'limit':10});self.assertTrue(r['ok']);self.assertEqual(len(r['indexed_ids']),3)
        status=self.g.TOOLS['gallery_visual_status'][1]({});self.assertEqual(status['indexed'],3)
        black=self.g.TOOLS['gallery_visual_search'][1]({'color':'black','limit':10});self.assertTrue(any(x['name']=='black.jpg' for x in black['items']))
    def test_categories_are_virtual(self):
        before={x['path']:x['mtime'] for x in self.g.gallery_list({'limit':20})['items']}
        cats=self.g.TOOLS['gallery_categories'][1]({});names={x['name']:x['count'] for x in cats['categories']};self.assertGreaterEqual(names['Screenshots'],1);self.assertGreaterEqual(names['Camera'],1)
        after={x['path']:x['mtime'] for x in self.g.gallery_list({'limit':20})['items']};self.assertEqual(before,after);self.assertFalse(cats['source_files_moved'])
    def test_persistent_exact_duplicate_index(self):
        # Add an exact byte-for-byte copy to exercise incremental hashing.
        import shutil
        source=self.g.SHARED_ROOT/'DCIM'/'Camera'/'black.jpg';copy=self.g.SHARED_ROOT/'copy-black.jpg';shutil.copy2(source,copy);self.g.gallery_scan({})
        while self.g.TOOLS['gallery_duplicate_status'][1]({})['remaining']:
            self.g.TOOLS['gallery_duplicate_index'][1]({'limit':20,'max_megabytes':32})
        groups=self.g.TOOLS['gallery_exact_duplicates'][1]({'limit':20})['groups']
        self.assertTrue(any(len(x['ids'])>=2 for x in groups))
        self.assertTrue(any(x['reclaimable_bytes']>0 for x in groups))

    def test_cleanup_plan_is_non_destructive(self):
        self.g.TOOLS['gallery_visual_index'][1]({'limit':10});items=self.g.gallery_list({'limit':10})['items'];ids=[x['id'] for x in items[:2]]
        plan=self.g.TOOLS['gallery_cleanup_plan'][1]({'ids':ids});self.assertTrue(plan['requires_user_approval']);self.assertFalse(plan['source_files_modified'])
        loaded=self.g.TOOLS['gallery_cleanup_plan_get'][1]({'plan_id':plan['plan_id']});self.assertEqual(loaded['state'],'PROPOSED')
    def test_semantic_unavailable_falls_back_to_local_exact_search(self):
        # No model is installed inside this isolated test environment.
        original=gallery_semantic.available; gallery_semantic.available=lambda:False
        try:
            status=self.g.TOOLS['gallery_semantic_status'][1]({});self.assertFalse(status['available'])
            result=self.g.TOOLS['gallery_smart_search'][1]({'query':'black.jpg','limit':10});self.assertTrue(result['ok']);self.assertTrue(any(x['name']=='black.jpg' for x in result['items']))
        finally:gallery_semantic.available=original

class SourceIsolationTests(AddonFeatureTests):
    def _add_remote(self, size=4096):
        with self.g._db() as c:
            cur=c.execute("INSERT INTO images(path,name,album,size,mtime,seen,mime) VALUES(?,?,?,?,?,?,?)",
                          ("shizuku:///storage/emulated/0/Android/data/demo/files/remote.jpg","remote.jpg","Android/data/demo",size,1234.0,1234.0,"image/jpeg"))
            return int(cur.lastrowid)

    def test_unavailable_shizuku_does_not_block_local_visual(self):
        import gallery_shizuku
        remote=self._add_remote(); original=gallery_shizuku.available_cached; gallery_shizuku.available_cached=lambda **_:False
        try:
            result=self.g.TOOLS['gallery_visual_index'][1]({'limit':20})
            self.assertEqual(len(result['indexed_ids']),3)
            self.assertIn('android_data_shizuku',result['deferred_sources'])
            status=self.g.TOOLS['gallery_visual_status'][1]({})
            self.assertEqual(status['local']['indexed'],3);self.assertEqual(status['android_data']['indexed'],0);self.assertEqual(status['android_data']['remaining'],1)
        finally:gallery_shizuku.available_cached=original

    def test_unavailable_shizuku_does_not_block_local_ocr(self):
        import gallery_shizuku
        self._add_remote(); original=gallery_shizuku.available_cached; gallery_shizuku.available_cached=lambda **_:False
        try:
            result=self.g.gallery_ocr_index({'languages':'eng','max_images':8,'max_seconds':30,'unrestricted':True})
            self.assertGreater(len(result['recognized_ids']),0)
            self.assertIsNone(result['paused']);self.assertIn('android_data_shizuku',result['deferred_sources']);self.assertEqual(result['shizuku_pending'],1)
        finally:gallery_shizuku.available_cached=original

    def test_unavailable_shizuku_does_not_block_local_semantic(self):
        import gallery_shizuku
        self._add_remote(); old_avail=gallery_shizuku.available_cached; old_model=gallery_semantic.available; old_embed=gallery_semantic.image_embedding
        class FakeEmbedding:
            def astype(self,*_):return self
            def tobytes(self):return b'0'*2048
        gallery_shizuku.available_cached=lambda **_:False;gallery_semantic.available=lambda:True;gallery_semantic.image_embedding=lambda _g,_row:FakeEmbedding()
        try:
            result=self.g.TOOLS['gallery_semantic_index'][1]({'limit':10})
            self.assertEqual(len(result['indexed_ids']),3);self.assertIn('android_data_shizuku',result['deferred_sources'])
            status=self.g.TOOLS['gallery_semantic_status'][1]({});self.assertEqual(status['local']['indexed'],3);self.assertEqual(status['android_data']['remaining'],1)
        finally:
            gallery_shizuku.available_cached=old_avail;gallery_semantic.available=old_model;gallery_semantic.image_embedding=old_embed

    def test_unavailable_shizuku_does_not_block_local_duplicate_hashing(self):
        import gallery_shizuku,shutil
        source=self.g.SHARED_ROOT/'DCIM'/'Camera'/'black.jpg';copy=self.g.SHARED_ROOT/'copy-black.jpg';shutil.copy2(source,copy);self.g.gallery_scan({})
        self._add_remote(size=source.stat().st_size); original=gallery_shizuku.available_cached;gallery_shizuku.available_cached=lambda **_:False
        try:
            result=self.g.TOOLS['gallery_duplicate_index'][1]({'limit':20,'max_megabytes':32})
            self.assertGreaterEqual(len(result['indexed_ids']),2);self.assertIn('android_data_shizuku',result['deferred_sources'])
            status=self.g.TOOLS['gallery_duplicate_status'][1]({});self.assertEqual(status['android_data']['remaining'],1);self.assertGreaterEqual(status['local']['indexed'],2)
        finally:gallery_shizuku.available_cached=original
