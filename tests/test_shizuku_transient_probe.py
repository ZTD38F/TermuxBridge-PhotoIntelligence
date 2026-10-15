"""Shizuku transient probes must be bounded and avoid source mutations.
Author: @dvkrum.
"""
import subprocess
import unittest
from unittest.mock import patch
import gallery_shizuku as shizuku

def response(rc):
    return subprocess.CompletedProcess(["rish"],rc)

class ShizukuTransientTests(unittest.TestCase):
    def setUp(self):
        self.old=(shizuku._AVAILABILITY_CHECKED_AT,shizuku._AVAILABILITY_VALUE)
        self.addCleanup(self.restore)
        shizuku._AVAILABILITY_CHECKED_AT=0
        shizuku._AVAILABILITY_VALUE=False
    def restore(self):
        shizuku._AVAILABILITY_CHECKED_AT,shizuku._AVAILABILITY_VALUE=self.old

    def test_transient_timeout_after_prior_success_recovers(self):
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run',side_effect=[subprocess.TimeoutExpired(["rish"],3),response(0)]) as run,patch.object(shizuku.time,'sleep') as sleep:
            self.assertTrue(shizuku.available_cached(ttl=30))
            self.assertEqual(run.call_count,2)
            self.assertEqual(run.call_args.kwargs["timeout"],5.)
            sleep.assert_called_once()
            self.assertTrue(shizuku._AVAILABILITY_VALUE)

    def test_verified_unavailability_after_two_failures(self):
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run',side_effect=[response(1),response(1)]) as run,patch.object(shizuku.time,'sleep'):
            self.assertFalse(shizuku.available_cached())
            self.assertEqual(run.call_count,2)

    def test_cold_unavailable_uses_one_probe(self):
        with patch.object(shizuku,'_run',return_value=response(1)) as run:
            self.assertFalse(shizuku.available_cached())
            run.assert_called_once()

    def test_force_bypasses_retry_and_cache(self):
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run',return_value=response(1)) as run:
            self.assertFalse(shizuku.available_cached(force=True,timeout=8))
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.kwargs["timeout"],8.0)

    def test_user_optout_never_invokes_rish_and_clears_cached_ready(self):
        shizuku._AVAILABILITY_VALUE=True
        shizuku._AVAILABILITY_CHECKED_AT=shizuku.time.monotonic()
        with patch.object(shizuku.photo_settings,'load',
                          return_value={'use_shizuku':False}), \
             patch.object(shizuku.subprocess,'run',
                          side_effect=AssertionError('rish must not run')) as run:
            self.assertFalse(shizuku.available_cached())
            self.assertFalse(shizuku.available_cached(force=True))
            self.assertFalse(shizuku._AVAILABILITY_VALUE)
            self.assertFalse(shizuku.enabled())
            with self.assertRaises(shizuku.ShizukuUnavailable):
                shizuku._run('test -d /storage/emulated/0/Android/data')
            with self.assertRaises(shizuku.ShizukuUnavailable):
                shizuku._remote_output('echo unsafe')
            with self.assertRaises(shizuku.ShizukuUnavailable):
                with shizuku.materialize(
                    'shizuku:///storage/emulated/0/Android/data/test/x.jpg',
                    7,100.0,shizuku.Path('/tmp')):
                    pass
            run.assert_not_called()

    def test_user_optout_settings_persistence_and_validation(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='photo-policy-test-') as d:
            settings_file=Path(d)/'settings.json'
            with patch.object(shizuku.photo_settings,'SETTINGS',settings_file):
                current=shizuku.photo_settings.update({'use_shizuku':False})
                self.assertFalse(current['use_shizuku'])
                self.assertFalse(shizuku.enabled())
                self.assertFalse(shizuku.photo_settings.load()['use_shizuku'])
                with self.assertRaises(ValueError):
                    shizuku.photo_settings.update({'use_shizuku':'false'})
                self.assertFalse(shizuku.photo_settings.load()['use_shizuku'])
                self.assertTrue(shizuku.photo_settings.update(
                    {'use_shizuku':True})['use_shizuku'])

    def test_ready_cached_does_not_spawn_process(self):
        shizuku._AVAILABILITY_CHECKED_AT=shizuku.time.monotonic()
        shizuku._AVAILABILITY_VALUE=True
        with patch.object(shizuku,'_run') as run:
            self.assertTrue(shizuku.available_cached(ttl=30))
            run.assert_not_called()

if __name__ == '__main__':
    unittest.main()
