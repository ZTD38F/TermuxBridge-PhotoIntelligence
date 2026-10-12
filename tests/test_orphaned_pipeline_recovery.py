"""Isolated stale-worker repair after addon version upgrades.
Author: @dvkrum.

Spawns only synthetic sleeping processes in a temporary fake releases tree.
Never interacts with the phone's gallery, current service or shared storage.
"""
from pathlib import Path
from unittest.mock import patch
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import service


def alive(pid):
    try:
        status=(Path('/proc')/str(pid)/'stat').read_text().rsplit(') ',1)[1].split()[0]
        return status!='Z'
    except FileNotFoundError:
        return False


class OldWorkerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='photo-reaper-test-')
        self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name)
        self.releases=base/'releases'
        self.previous=self.releases/'0.6.3'
        self.current=self.releases/'0.6.4'
        (self.previous/'runtime').mkdir(parents=True)
        (self.current/'runtime').mkdir(parents=True)
        (self.previous/'addon.json').write_text(json.dumps({
            'name':'photo-intelligence','version':'0.6.3'
        }))
        self.state=base/'state'
        self.state.mkdir()
        self.patch_current=patch.object(service,'ROOT',self.current)
        self.patch_state=patch.object(service,'STATE_DIR',self.state)
        self.patch_current.start();self.patch_state.start()
        self.addCleanup(self.patch_current.stop)
        self.addCleanup(self.patch_state.stop)

    def worker(self, release, ignore_sigterm=False):
        src=release/'runtime/pipeline_scheduler.py'
        src.write_text('''import signal,time
signal.signal(signal.SIGTERM,signal.SIG_IGN) if %s else None
while True: time.sleep(.3)
''' % repr(ignore_sigterm))
        code='''import subprocess,sys
p=subprocess.Popen([sys.executable,sys.argv[1]],
 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
 start_new_session=True,close_fds=True)
print(p.pid,flush=True)
'''
        launcher=subprocess.run([sys.executable,'-c',code,str(src)],check=True,
                                text=True,capture_output=True,timeout=8)
        pid=int(launcher.stdout.strip())
        deadline=time.monotonic()+4
        last='none'
        while time.monotonic()<deadline:
            try:
                status=service._owned_pipeline(pid,src)
                last=f'parent={status[0]}'
                if status[0]==1:
                    return pid
            except (OSError,RuntimeError) as exc:
                last=f'{type(exc).__name__}:{str(exc)[:100]}'
            time.sleep(.05)
        self.cleanup_worker(pid)
        raise RuntimeError('isolated orphan did not detach: '+last)

    def cleanup_worker(self,pid):
        if alive(pid):
            try:os.killpg(pid,signal.SIGKILL)
            except ProcessLookupError:pass

    def test_stale_worker_is_reaped_and_lock_can_be_reclaimed(self):
        pid=self.worker(self.previous)
        self.addCleanup(self.cleanup_worker,pid)
        (self.state/'pipeline_scheduler.pid').write_text(str(pid))
        self.assertTrue(service.recover_orphaned_pipeline())
        deadline=time.monotonic()+3
        while alive(pid) and time.monotonic()<deadline:time.sleep(.05)
        self.assertFalse(alive(pid))

    def test_noncooperative_worker_is_reaped_after_bounded_grace(self):
        pid=self.worker(self.previous,ignore_sigterm=True)
        self.addCleanup(self.cleanup_worker,pid)
        (self.state/'pipeline_scheduler.pid').write_text(str(pid))
        # Let synthetic worker install SIGTERM handler before requesting shutdown.
        time.sleep(.3)
        started=time.monotonic()
        self.assertTrue(service.recover_orphaned_pipeline())
        self.assertLess(time.monotonic()-started,8)
        self.assertFalse(alive(pid))

    def test_current_release_worker_is_preserved(self):
        pid=self.worker(self.current)
        self.addCleanup(self.cleanup_worker,pid)
        (self.state/'pipeline_scheduler.pid').write_text(str(pid))
        self.assertFalse(service.recover_orphaned_pipeline())
        self.assertTrue(alive(pid))

    def test_unrecognized_script_never_receives_signal(self):
        other=self.releases.parent/'unrelated'
        (other/'runtime').mkdir(parents=True)
        pid=self.worker(other)
        self.addCleanup(self.cleanup_worker,pid)
        (self.state/'pipeline_scheduler.pid').write_text(str(pid))
        with self.assertRaisesRegex(RuntimeError,'unknown scheduler'):
            service.recover_orphaned_pipeline()
        self.assertTrue(alive(pid))

    def test_status_refuses_false_healthy_pid(self):
        class P:
            pid=777
            def poll(self): return None
        self.assertFalse(service._pipeline_ready({'pipeline':P()}))

if __name__=='__main__':
    unittest.main()
