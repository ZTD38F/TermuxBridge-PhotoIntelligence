from __future__ import annotations
import importlib,json,os,sqlite3,tempfile,time,unittest
from pathlib import Path

class PipelineFoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'state';self.shared=self.root/'shared';self.state.mkdir();self.shared.mkdir()
        self.old_env=os.environ.copy();os.environ['PHOTO_INTELLIGENCE_HOME']=str(self.root/'home');os.environ['PHOTO_INTELLIGENCE_STATE']=str(self.state);os.environ['PHOTO_INTELLIGENCE_SHARED_ROOT']=str(self.shared)
        import photo_config,photo_settings,db_migrations,source_watcher,pipeline_scheduler
        for m in (photo_config,photo_settings,db_migrations,source_watcher,pipeline_scheduler):importlib.reload(m)
        self.cfg=photo_config;self.settings=photo_settings;self.migrations=db_migrations;self.watcher=source_watcher;self.scheduler=pipeline_scheduler
        self.addCleanup(self.restore_env)
    def restore_env(self):
        os.environ.clear();os.environ.update(self.old_env)
    def test_profiles_and_atomic_settings(self):
        self.assertEqual(self.settings.load()['profile'],'balanced')
        eff=self.settings.update({'profile':'unlimited','watch_local':False})
        self.assertEqual(eff['profile'],'unlimited');self.assertEqual(eff['min_battery'],1);self.assertFalse(self.settings.load()['watch_local'])
        with self.assertRaises(ValueError):self.settings.update({'profile':'turbo'})
    def test_migration_sets_version_and_backup(self):
        db=self.state/'gallery.sqlite3'
        with sqlite3.connect(db) as c:c.execute('create table images(id integer primary key,path text)');c.commit()
        r=self.migrations.migrate(db);self.assertTrue(r['ok']);self.assertEqual(r['to'],1);self.assertTrue(Path(r['backup']).is_file())
        with sqlite3.connect(db) as c:self.assertEqual(c.execute('pragma user_version').fetchone()[0],1)
        r2=self.migrations.migrate(db);self.assertIsNone(r2['backup'])
    def test_watcher_dirty_marker_contains_no_content(self):
        p=self.shared/'DCIM'/'Camera';p.mkdir(parents=True);f=p/'x.jpg';f.write_bytes(b'secret-photo-bytes')
        self.watcher.mark('test',str(f));d=json.loads((self.state/'source_dirty.json').read_text())
        self.assertTrue(d['dirty']);self.assertEqual(d['path'],'DCIM/Camera/x.jpg');self.assertNotIn('secret-photo-bytes',json.dumps(d))
    def test_power_profiles(self):
        balanced={**self.settings.PROFILES['balanced'],'profile':'balanced'}
        self.assertEqual(self.scheduler.power_allowed(balanced,{'available':True,'level':5,'charging':False,'temperature_c':30})[1],'BATTERY')
        unlimited={**self.settings.PROFILES['unlimited'],'profile':'unlimited'}
        self.assertTrue(self.scheduler.power_allowed(unlimited,{'available':True,'level':1,'charging':False,'temperature_c':60})[0])
    def test_event_triggers_scan(self):
        cfg={**self.settings.effective(),'auto_scan':True}
        self.assertTrue(self.scheduler.due_scan(cfg,None)[0])
        self.scheduler.DIRTY.write_text('{}')
        self.assertEqual(self.scheduler.due_scan(cfg,time.time())[1],'event')

class PipelineHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'state';self.state.mkdir()
        self.old_env=os.environ.copy();os.environ['PHOTO_INTELLIGENCE_HOME']=str(self.root/'home');os.environ['PHOTO_INTELLIGENCE_STATE']=str(self.state)
        import photo_config,pipeline_scheduler
        importlib.reload(photo_config);importlib.reload(pipeline_scheduler)
        self.scheduler=pipeline_scheduler;self.addCleanup(self.restore)
    def restore(self):os.environ.clear();os.environ.update(self.old_env)
    def test_long_stage_writes_heartbeat(self):
        import threading
        cfg={'profile':'balanced'};b={'available':True,'level':50,'charging':False,'temperature_c':30};metrics={}
        out={}
        def slow():time.sleep(5.4);return {'ok':True,'indexed_ids':[1]}
        t=threading.Thread(target=lambda:out.setdefault('r',self.scheduler.run_stage('semantic',slow,metrics,cfg,b)));t.start();time.sleep(5.15)
        hb=json.loads(self.scheduler.STATUS.read_text());self.assertEqual(hb['state'],'RUNNING');self.assertEqual(hb['phase'],'semantic');self.assertGreater(hb['timestamp'],0)
        t.join(timeout=3);self.assertFalse(t.is_alive());self.assertTrue(out['r'][0]['ok'])
