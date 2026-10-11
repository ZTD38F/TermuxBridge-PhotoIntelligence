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

class SearchV030Tests(AddonFeatureTests):
    def test_multilingual_visual_query_variants(self):
        import gallery_smart_search
        self.assertEqual(gallery_smart_search.semantic_variants('чёрная рубашка')[1],'black shirt')
        self.assertEqual(gallery_smart_search.semantic_variants('melns krekls')[1],'black shirt')

    def test_hybrid_metadata_ranking_and_reasons(self):
        result=self.g.TOOLS['gallery_hybrid_search'][1]({'query':'black.jpg','limit':10})
        self.assertTrue(result['ok']);self.assertGreaterEqual(result['count'],1)
        self.assertEqual(result['items'][0]['name'],'black.jpg')
        self.assertIn('metadata',result['items'][0]['match'])
        self.assertTrue(result['items'][0]['reasons'])

    def test_hybrid_ocr_signal(self):
        target=next(x for x in self.g.gallery_list({'limit':20})['items'] if x['name']=='screenshot_1.png')
        with self.g._db() as c:
            self.g.gallery_ocr_status({})
            c.execute("INSERT OR REPLACE INTO gallery_ocr_index(image_id,size,mtime,languages,processed_at,text_length,error) VALUES(?,?,?,?,?,?,NULL)",(target['id'],target['size'],target['mtime'],'rus+lav+eng',1234.0,14))
            c.execute("DELETE FROM gallery_ocr_fts WHERE rowid=?",(target['id'],))
            c.execute("INSERT INTO gallery_ocr_fts(rowid,content) VALUES(?,?)",(target['id'],'AMAZON RECEIPT'))
        result=self.g.TOOLS['gallery_hybrid_search'][1]({'query':'AMAZON RECEIPT','limit':10})
        found=next(x for x in result['items'] if x['id']==target['id']);self.assertIn('ocr',found['match'])

    def test_search_contact_sheet(self):
        result=self.g.TOOLS['gallery_search_contact_sheet'][1]({'query':'black.jpg','limit':4,'columns':2})
        content=result['__mcp_content__'];self.assertEqual(content[0]['type'],'image');self.assertEqual(content[0]['mimeType'],'image/jpeg');self.assertGreater(len(content[0]['data']),100)
        import json as _json
        meta=_json.loads(content[1]['text']);self.assertGreaterEqual(meta['count'],1);self.assertEqual(meta['search_mode'],'hybrid-rrf-v2')

class CleanupV040Tests(AddonFeatureTests):
    def _item(self,name):
        return next(x for x in self.g.gallery_list({'limit':50})['items'] if x['name']==name)
    def _confirm(self,tool,plan_id):
        return self.g.TOOLS[tool][1]({'plan_id':plan_id,'approved':True,'confirm_plan_id':plan_id})

    def test_quarantine_then_restore_roundtrip_and_ocr_cleanup(self):
        item=self._item('black.jpg');source=self.g.SHARED_ROOT/item['path'];self.assertTrue(source.is_file())
        self.g.gallery_ocr_status({})
        with self.g._db() as c:
            c.execute("INSERT OR REPLACE INTO gallery_ocr_index(image_id,size,mtime,languages,processed_at,text_length,error) VALUES(?,?,?,?,?,?,NULL)",(item['id'],item['size'],item['mtime'],'rus+lav+eng',1234.0,4))
            c.execute("INSERT OR REPLACE INTO gallery_ocr_fts(rowid,content) VALUES(?,?)",(item['id'],'TEST'))
        plan=self.g.TOOLS['gallery_cleanup_plan'][1]({'ids':[item['id']],'undo_hours':1});pid=plan['plan_id']
        with self.assertRaises(PermissionError):self.g.TOOLS['gallery_cleanup_quarantine'][1]({'plan_id':pid,'approved':False,'confirm_plan_id':pid})
        result=self._confirm('gallery_cleanup_quarantine',pid);self.assertEqual(result['state'],'QUARANTINED');self.assertFalse(source.exists())
        loaded=self.g.TOOLS['gallery_cleanup_plan_get'][1]({'plan_id':pid});self.assertEqual(loaded['state'],'QUARANTINED');q=self.g.SHARED_ROOT/loaded['actions'][0]['quarantine_path'];self.assertTrue(q.is_file())
        with self.g._db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM images WHERE id=?',(item['id'],)).fetchone());self.assertIsNone(c.execute('SELECT 1 FROM gallery_ocr_index WHERE image_id=?',(item['id'],)).fetchone());self.assertIsNone(c.execute('SELECT 1 FROM gallery_ocr_fts WHERE rowid=?',(item['id'],)).fetchone())
        restored=self._confirm('gallery_cleanup_restore',pid);self.assertEqual(restored['state'],'RESTORED');self.assertTrue(source.is_file());self.assertFalse(q.exists())
        scan=self.g.gallery_scan({});self.assertTrue(scan['ok']);self.assertTrue(any(x['name']=='black.jpg' for x in self.g.gallery_list({'limit':50})['items']))

    def test_purge_requires_undo_window_then_is_irreversible(self):
        item=self._item('screenshot_1.png');source=self.g.SHARED_ROOT/item['path'];plan=self.g.TOOLS['gallery_cleanup_plan'][1]({'ids':[item['id']],'undo_hours':1});pid=plan['plan_id'];self._confirm('gallery_cleanup_quarantine',pid)
        with self.assertRaises(PermissionError):self._confirm('gallery_cleanup_purge',pid)
        with self.g._db() as c:
            row=c.execute('SELECT payload FROM gallery_cleanup_plans WHERE plan_id=?',(pid,)).fetchone();payload=__import__('json').loads(row['payload']);payload['purge_after']=0;c.execute('UPDATE gallery_cleanup_plans SET payload=? WHERE plan_id=?',(__import__('json').dumps(payload,separators=(',',':')),pid))
        result=self._confirm('gallery_cleanup_purge',pid);self.assertTrue(result['ok']);self.assertEqual(result['state'],'PURGED');self.assertFalse(source.exists());self.assertTrue(result['irreversible'])
        loaded=self.g.TOOLS['gallery_cleanup_plan_get'][1]({'plan_id':pid});self.assertEqual(loaded['state'],'PURGED');self.assertTrue(all(a['state']=='PURGED' for a in loaded['actions']))

    def test_android_data_plan_cannot_mutate(self):
        with self.g._db() as c:
            iid=c.execute("INSERT INTO images(path,name,album,size,mtime,seen,mime) VALUES(?,?,?,?,?,?,?)",('shizuku:///storage/emulated/0/Android/data/demo/files/x.jpg','x.jpg','Android/data/demo',10,1.0,1,'image/jpeg')).lastrowid
        plan=self.g.TOOLS['gallery_cleanup_plan'][1]({'ids':[iid]});pid=plan['plan_id']
        with self.assertRaises(PermissionError):self._confirm('gallery_cleanup_quarantine',pid)
        self.assertEqual(self.g.TOOLS['gallery_cleanup_plan_get'][1]({'plan_id':pid})['state'],'PROPOSED')

    def test_quarantine_symlink_root_is_refused_before_move(self):
        item=self._item('black.jpg');source=self.g.SHARED_ROOT/item['path'];outside=self.g.SHARED_ROOT.parent/'outside-quarantine';outside.mkdir(exist_ok=True)
        qroot=self.g.SHARED_ROOT/'.photo-intelligence-quarantine';qroot.symlink_to(outside,target_is_directory=True)
        plan=self.g.TOOLS['gallery_cleanup_plan'][1]({'ids':[item['id']]});pid=plan['plan_id']
        with self.assertRaises(ValueError):self._confirm('gallery_cleanup_quarantine',pid)
        self.assertTrue(source.is_file());self.assertEqual(self.g.TOOLS['gallery_cleanup_plan_get'][1]({'plan_id':pid})['state'],'PROPOSED')
