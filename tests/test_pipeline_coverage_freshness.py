"""Read-only, rate-limited coverage freshness during long stages.
Author: @dvkrum. All test state is synthetic and isolated.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
import gallery_pipeline
import pipeline_scheduler as scheduler


class CoverageFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='photo-coverage-test-')
        self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        self.status=self.state/'pipeline_status.json'
        self.p1=patch.object(scheduler,'STATUS',self.status)
        self.p2=patch.object(gallery_pipeline,'STATE_DIR',self.state)
        self.p1.start();self.p2.start()
        self.addCleanup(self.p1.stop);self.addCleanup(self.p2.stop)
        self.original_last=scheduler._LAST_COVERAGE_CHECK
        self.addCleanup(setattr,scheduler,'_LAST_COVERAGE_CHECK',self.original_last)
        scheduler._LAST_COVERAGE_CHECK=0.0

    def heartbeat(self):
        scheduler._heartbeat('ocr',{'profile':'unlimited'},
                             {'available':False,'level':100,'charging':False})

    def read(self):
        return json.loads(self.status.read_text())

    def test_running_stage_refreshes_coverage_once_per_minute(self):
        counts=[{'ocr':{'processed':4},'library':10},
                {'ocr':{'processed':9},'library':10}]
        hits=[]
        def fake_coverage():
            hits.append(1)
            return counts[min(len(hits)-1,1)]
        with patch.object(scheduler,'coverage',side_effect=fake_coverage):
            self.heartbeat()
            first=self.read()
            self.assertEqual(first['coverage']['ocr']['processed'],4)
            self.assertFalse(first['coverage_stale'])
            self.assertLess(abs(first['coverage_updated_at']-time.time()),5)
            self.heartbeat()
            self.assertEqual(len(hits),1)
            self.assertEqual(self.read()['coverage']['ocr']['processed'],4)
            # Simulate a long stage without a real one-minute sleep.
            scheduler._LAST_COVERAGE_CHECK-=61
            self.heartbeat()
            self.assertEqual(len(hits),2)
            self.assertEqual(self.read()['coverage']['ocr']['processed'],9)
            self.assertFalse(self.read()['coverage_stale'])

    def test_slow_coverage_query_cannot_delay_initial_heartbeat(self):
        import threading
        query_started=threading.Event()
        release_query=threading.Event()
        def simulated_slow_query():
            query_started.set()
            if not release_query.wait(3):
                raise TimeoutError('synthetic slow count')
            return {'ocr':{'processed':7}}
        with patch.object(scheduler,'coverage',side_effect=simulated_slow_query):
            thread=threading.Thread(target=self.heartbeat,daemon=True)
            thread.start()
            try:
                self.assertTrue(query_started.wait(1))
                # Even while the snapshot is blocked, a fresh liveness marker exists.
                transient=self.read()
                self.assertEqual(transient['state'],'RUNNING')
                self.assertEqual(transient['phase'],'ocr')
                self.assertTrue(transient['coverage_stale'])
                self.assertLess(abs(transient['timestamp']-time.time()),3)
            finally:
                release_query.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(self.read()['coverage_stale'])
            self.assertEqual(self.read()['coverage']['ocr']['processed'],7)

    def test_failure_retains_old_snapshot_and_marks_stale(self):
        self.status.write_text(json.dumps({'coverage':{'ocr':{'processed':3}},
            'coverage_updated_at':time.time()-180, 'timestamp':time.time()}))
        with patch.object(scheduler,'coverage',side_effect=RuntimeError('synthetic')):
            self.heartbeat()
        p=self.read()
        self.assertTrue(p['coverage_stale'])
        self.assertEqual(p['coverage']['ocr']['processed'],3)
        self.assertEqual(p['coverage_refresh_error'],'COVERAGE_UNAVAILABLE')
        self.assertEqual(p['state'],'RUNNING')
        self.assertLess(abs(time.time()-p['timestamp']),5)

    def test_pipeline_api_marks_legacy_recent_heartbeat_counts_stale(self):
        self.status.write_text(json.dumps({'ok':True,'state':'RUNNING',
            'timestamp':time.time(),'coverage':{'ocr':{'processed':3}}}))
        d=self.tool()
        p=d['pipeline']
        self.assertTrue(p['coverage_stale'])
        self.assertIsNone(p['coverage_updated_at'])
        self.assertIsNone(p['coverage_age_seconds'])
        self.assertEqual(p['coverage']['ocr']['processed'],3)

    def test_pipeline_api_live_and_old_freshness(self):
        t=time.time()
        self.status.write_text(json.dumps({'coverage_updated_at':t-30,'timestamp':t}))
        p=self.tool()['pipeline']
        self.assertFalse(p['coverage_stale'])
        self.assertGreater(p['coverage_age_seconds'],20)
        self.status.write_text(json.dumps({'coverage_updated_at':t-200,'timestamp':t}))
        self.assertTrue(self.tool()['pipeline']['coverage_stale'])

    def test_only_synthetic_scheduler_state_is_written(self):
        with patch.object(scheduler,'coverage',return_value={'library':0}):
            self.heartbeat()
        self.assertEqual({p.name for p in self.state.iterdir()},{'pipeline_status.json'})
        self.assertEqual(self.read()['coverage']['library'],0)

    def tool(self):
        schemas=lambda *args,**kwargs:{}
        d={'_schema':schemas,'TOOLS':{},'DESCRIPTIONS':{},'READ_ONLY':set()}
        gallery_pipeline.install(d)
        return d['TOOLS']['gallery_pipeline_status'][1]({})


if __name__=='__main__':
    unittest.main()
